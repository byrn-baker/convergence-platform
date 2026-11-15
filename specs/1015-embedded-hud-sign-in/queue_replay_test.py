"""Run: python3 -B specs/1015-embedded-hud-sign-in/queue_replay_test.py -v

Spec 1015 design fixture. The legacy checks use the shipped JobStore. For the
proposal, separate SQLite files stand in for the PostgreSQL reviewer API and
the receiver. Job admission and receipts are modeled here; claiming, restart,
dispatch checkpoints and terminal job cleanup use the shipped JobStore. A
list records calls to a synthetic hook. No network, credentials, or daemon.
"""

import hashlib
import json
import sqlite3
import sys
import time
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services/alert-receiver"))
from jobs import JobStore


def digest_of(body):
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class Conflict(ValueError):
    """A previously accepted ID was presented with different inputs."""


@dataclass(frozen=True)
class Command:
    # Actor and installation represent server-verified identities in this fixture.
    actor: str
    operation_id: str
    feedback: str
    installation: str = "review-hud"
    site: str = "lab"
    case: str = "842eaf26-6ab7-4d87-98b0-b068da7d95fb"
    action: str = "reinvestigate"

    def body(self):
        return {"version": 1, "actor": self.actor, "installation": self.installation,
                "operation_id": self.operation_id, "site": self.site, "event_id": self.case,
                "action": self.action, "expert_feedback": self.feedback.strip()}


class ReviewerAPI:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS commands (
                installation TEXT, actor TEXT, operation TEXT, digest TEXT, delivery TEXT UNIQUE,
                PRIMARY KEY (installation, actor, operation));
            CREATE TABLE IF NOT EXISTS notes (actor TEXT, site TEXT, case_id TEXT, text TEXT);
            CREATE TABLE IF NOT EXISTS outbox (delivery TEXT PRIMARY KEY, body TEXT, job_id TEXT);
        """)

    def accept(self, command):
        digest = digest_of(command.body())
        delivery = str(uuid4())
        scope = (command.installation, command.actor, command.operation_id)
        with self.db:
            inserted = self.db.execute(
                "INSERT INTO commands VALUES (?,?,?,?,?) ON CONFLICT (installation,actor,operation) DO NOTHING",
                (*scope, digest, delivery))
            if not inserted.rowcount:
                prior_digest, prior_delivery = self.db.execute(
                    "SELECT digest, delivery FROM commands WHERE installation=? AND actor=? AND operation=?",
                    scope).fetchone()
                if prior_digest != digest:
                    raise Conflict("command inputs changed")
                return prior_delivery
            self.db.execute("INSERT INTO notes VALUES (?,?,?,?)",
                            (command.actor, command.site, command.case, command.feedback.strip()))
            self.db.execute("INSERT INTO outbox VALUES (?,?,NULL)",
                            (delivery, json.dumps(command.body())))
        return delivery

    def deliver(self, delivery, receiver, *, lose_response=False):
        body, = self.db.execute("SELECT body FROM outbox WHERE delivery=?", (delivery,)).fetchone()
        job_id = receiver.receive(delivery, json.loads(body))
        if lose_response:
            raise TimeoutError("receiver committed, relay did not receive the acknowledgement")
        with self.db:
            self.db.execute("UPDATE outbox SET job_id=? WHERE delivery=?", (job_id, delivery))
        return job_id


class Receiver:
    def __init__(self, path):
        self.jobs = JobStore(path)
        self.db = self.jobs.db
        self.db.execute("""CREATE TABLE IF NOT EXISTS receipts (
            delivery TEXT PRIMARY KEY, digest TEXT, job_id TEXT UNIQUE, status TEXT)""")

    def receive(self, delivery, body):
        digest = digest_of(body)
        job_id = str(uuid4())
        with self.db:
            inserted = self.db.execute(
                "INSERT INTO receipts VALUES (?,?,?,'enqueued') ON CONFLICT (delivery) DO NOTHING",
                (delivery, digest, job_id))
            if not inserted.rowcount:
                prior = self.db.execute("SELECT digest, job_id FROM receipts WHERE delivery=?", (delivery,)).fetchone()
                if prior["digest"] != digest:
                    raise Conflict("delivery inputs changed")
                return prior["job_id"]
            # A real implementation needs a transaction-aware bounded admission
            # method on JobStore. Its public enqueue() commits its own transaction.
            now = time.time()
            self.db.execute("INSERT INTO jobs (id,key,kind,payload,created,available) VALUES (?,?,'manual',?,?,?)",
                            (job_id, delivery, json.dumps(body), now, now))
        return job_id

    def dispatch(self, calls, *, lose_response=False):
        job = self.jobs.claim()
        if job is None:
            return None
        if job["dispatched"] is not None:
            return "needs reconciliation"
        body = json.loads(job["payload"])
        self.jobs.checkpoint(job["id"], body["event_id"], body["site"])
        calls.append(job["id"])
        if lose_response:
            raise TimeoutError("synthetic hook called, receiver did not receive its outcome")
        self.complete(job["id"])
        return job["id"]

    def complete(self, job_id):
        # The fixture supplies a correlated, successful synthetic outcome.
        with self.db:
            self.db.execute("UPDATE jobs SET state='done' WHERE id=?", (job_id,))
            self.db.execute("UPDATE receipts SET status='completed' WHERE job_id=?", (job_id,))


class ReplayTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.api = ReviewerAPI(self.folder / "api.sqlite")
        self.receiver = Receiver(self.folder / "receiver.sqlite")
        self.addCleanup(lambda: self.api.db.close())
        self.addCleanup(lambda: self.receiver.db.close())
        self.command = Command("reviewer-a", str(uuid4()), "Inspect the first flow.")
        self.calls = []

    def restart(self):
        self.api.db.close()
        self.receiver.db.close()
        self.api = ReviewerAPI(self.folder / "api.sqlite")
        self.receiver = Receiver(self.folder / "receiver.sqlite")

    def assert_effects(self, commands, notes, dispatches):
        self.assertEqual(self.api.db.execute("SELECT count(*) FROM commands").fetchone()[0], commands)
        self.assertEqual(self.api.db.execute("SELECT count(*) FROM notes").fetchone()[0], notes)
        self.assertEqual(len(self.calls), dispatches)

    def test_legacy_manual_key_coalesces_different_feedback_then_replays_after_completion(self):
        jobs = self.receiver.jobs
        body = {"event_id": self.command.case, "site": self.command.site,
                "expert_feedback": self.command.feedback}
        key = f"manual:{body['site']}:{body['event_id']}"
        first = jobs.enqueue(key, "manual", body, repeat=True)
        second = {**body, "expert_feedback": "Inspect a second flow."}
        self.assertEqual(jobs.enqueue(key, "manual", second, repeat=True), first)
        self.assertEqual(json.loads(jobs.claim()["payload"])["expert_feedback"], self.command.feedback)
        jobs.finish(first)
        self.restart()
        self.assertNotEqual(self.receiver.jobs.enqueue(key, "manual", body, repeat=True), first)

    def test_legacy_jev_replay_reopens_failed_case_job(self):
        jobs = self.receiver.jobs
        body = {"event_id": self.command.case, "site": self.command.site}
        key = f"jev:{body['site']}:{body['event_id']}"
        first = jobs.enqueue(key, "jev_review", body, retry_failed=True)
        jobs.finish(first, "synthetic failure")
        self.restart()
        jobs = self.receiver.jobs
        self.assertEqual(jobs.enqueue(key, "jev_review", body, retry_failed=True), first)
        self.assertEqual(jobs.claim("review")["id"], first)

    def test_response_loss_restart_cleanup_and_replay(self):
        delivery = self.api.accept(self.command)
        with self.assertRaises(TimeoutError):
            self.api.deliver(delivery, self.receiver, lose_response=True)
        self.assertIsNone(self.api.db.execute("SELECT job_id FROM outbox").fetchone()[0])
        row = self.receiver.db.execute("SELECT job_id,status FROM receipts").fetchone()
        self.assertEqual(row["status"], "enqueued")
        self.assert_effects(1, 1, 0)

        self.assertEqual(self.receiver.dispatch(self.calls), row["job_id"])
        with self.receiver.db:
            self.receiver.db.execute("UPDATE jobs SET created=0 WHERE id=?", (row["job_id"],))
        self.receiver.jobs.enqueue("unrelated", "snapshot", {})  # runs the real terminal-job pruning
        self.assertIsNone(self.receiver.db.execute("SELECT id FROM jobs WHERE id=?", (row["job_id"],)).fetchone())
        self.restart()
        self.assertEqual(self.api.accept(self.command), delivery)
        self.assertEqual(self.api.deliver(delivery, self.receiver), row["job_id"])
        self.assertIsNone(self.receiver.dispatch(self.calls))
        self.assertEqual(self.receiver.db.execute("SELECT status FROM receipts").fetchone()[0], "completed")
        self.assert_effects(1, 1, 1)

    def test_two_reviewers_and_conflicting_reuse(self):
        first = self.api.accept(self.command)
        other = replace(self.command, actor="reviewer-b", feedback="Inspect the second flow.")
        second = self.api.accept(other)  # same operation UUID, independently scoped to the second actor
        self.assertNotEqual(first, second)
        self.assertEqual(self.api.accept(replace(self.command, feedback="  " + self.command.feedback)), first)
        for change in ({"feedback": "Inspect the second flow."}, {"site": "other"},
                       {"case": str(uuid4())}, {"action": "jev_review"}):
            with self.subTest(change=change), self.assertRaises(Conflict):
                self.api.accept(replace(self.command, **change))
        first_job = self.api.deliver(first, self.receiver)
        second_job = self.api.deliver(second, self.receiver)
        self.assertNotEqual(first_job, second_job)
        with self.assertRaises(Conflict):
            self.receiver.receive(first, other.body())
        self.receiver.dispatch(self.calls)
        self.receiver.dispatch(self.calls)
        self.assertCountEqual(self.calls, [first_job, second_job])
        self.assertCountEqual(self.api.db.execute("SELECT actor,text FROM notes").fetchall(),
                              [(self.command.actor, self.command.feedback), (other.actor, other.feedback)])
        self.assert_effects(2, 2, 2)

    def test_lost_hook_response_waits_for_correlated_evidence_after_restart(self):
        delivery = self.api.accept(self.command)
        job_id = self.api.deliver(delivery, self.receiver)
        with self.assertRaises(TimeoutError):
            self.receiver.dispatch(self.calls, lose_response=True)
        self.restart()
        self.assertEqual(self.api.deliver(delivery, self.receiver), job_id)
        self.assertEqual(self.receiver.dispatch(self.calls), "needs reconciliation")
        self.assertEqual(self.calls, [job_id])
        self.assert_effects(1, 1, 1)

    def test_partial_commits_roll_back_on_either_side(self):
        self.api.db.execute("""CREATE TEMP TRIGGER fail_outbox BEFORE INSERT ON outbox
            BEGIN SELECT RAISE(ABORT,'synthetic outbox failure'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic outbox failure"):
            self.api.accept(self.command)
        self.assert_effects(0, 0, 0)
        self.api.db.execute("DROP TRIGGER fail_outbox")
        delivery = self.api.accept(self.command)
        self.receiver.db.execute("""CREATE TEMP TRIGGER fail_job BEFORE INSERT ON jobs
            BEGIN SELECT RAISE(ABORT,'synthetic admission failure'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic admission failure"):
            self.api.deliver(delivery, self.receiver)
        self.assertEqual(self.receiver.db.execute("SELECT count(*) FROM receipts").fetchone()[0], 0)
        self.assert_effects(1, 1, 0)


if __name__ == "__main__":
    unittest.main()
