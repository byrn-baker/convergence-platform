"""Scoped case interface for investigators without shell or arbitrary HTTP access."""
import os
import uuid
import httpx
from fastmcp import FastMCP
mcp=FastMCP('convergence-case-mcp')
BASE=(os.getenv('CONVERGENCE_API_URL') or os.getenv('HOME_API_URL') or os.getenv('NETWORK_GUARDIAN_URL','')).rstrip('/')
TOKEN=os.getenv('CONVERGENCE_API_TOKEN') or os.getenv('HOME_API_TOKEN') or os.getenv('NETWORK_GUARDIAN_TOKEN','')
SITE=os.getenv('GUARDIAN_DEFAULT_SITE','home')

async def request(method, event_id, body=None, action=''):
    event_id=str(uuid.UUID(event_id))
    if not BASE or not TOKEN: raise ValueError('Case interface is not configured')
    async with httpx.AsyncClient(timeout=15) as client:
        r=await client.request(method,f'{BASE}/api/events/{event_id}{action}',params={'site':SITE},
            headers={'Authorization':f'Bearer {TOKEN}'},json=body)
        r.raise_for_status()
        return r.json()

@mcp.tool()
async def case_get(event_id: str) -> dict:
    """Read the assigned investigation case and operator feedback."""
    return await request('GET',event_id)

@mcp.tool()
async def flow_top_talkers(time_range: str = '1h', exporter: str = '') -> dict:
    """Read top source/destination addresses and pairs from this configured site's retained flow exports.
    time_range: 15m, 1h, 6h or 24h. Exporter must belong to this site (empty selects all).
    Totals are observed exported bytes, not billing usage or proof of malicious activity.
    """
    if time_range not in ('15m','1h','6h','24h') or len(exporter)>64:
        raise ValueError('Invalid flow filter')
    return await flow_read('', {'range': time_range, 'exporter': exporter})

@mcp.tool()
async def flow_evidence(address: str, start: str, end: str, exporter: str = '') -> dict:
    """Read up to 100 recent matching flow records for one IP and an explicit ISO time window (max 24h).
    This configured site is enforced. Confirm the returned site matches the investigation.
    Times select collector receipt, not connection start. Empty results do not prove no traffic.
    Use alongside case_get and available endpoint/DNS/TLS evidence for formal case deep dives.
    """
    import ipaddress
    ipaddress.ip_address(address)
    if len(start)>40 or len(end)>40 or len(exporter)>64: raise ValueError('Invalid flow window')
    return await flow_read('/records', {'address': address, 'start': start, 'end': end, 'exporter': exporter})

async def flow_read(path, params):
    if not BASE or not TOKEN: raise ValueError('Case interface is not configured')
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f'{BASE}/api/flows{path}', params={**params, 'site': SITE},
                                    headers={'Authorization': f'Bearer {TOKEN}'})
        response.raise_for_status()
        return response.json()

@mcp.tool()
async def isp_provider_catalog() -> dict:
    """List supported US providers and dated official support/credit source links.
    Selecting a recipient does not identify the failing WAN or establish credit eligibility.
    """
    if not BASE or not TOKEN: raise ValueError('Case interface is not configured')
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f'{BASE}/api/isp/providers', params={'site': SITE},
                                    headers={'Authorization': f'Bearer {TOKEN}'})
        response.raise_for_status()
        return response.json()

@mcp.tool()
async def case_isp_report(event_id: str, provider_id: str, timezone: str = 'UTC') -> dict:
    """Prepare and save a local provider-message draft and redacted evidence for a WAN case.
    Choose provider_id from isp_provider_catalog after confirming the affected service.
    Uses stored incident times and bounded historical probe measurements; missing data is explicit.
    No email is sent, claim filed, account accessed or credit promised. Ask the user to review
    the draft, recipient, applicable terms and evidence before any separate sending action.
    Re-generating replaces the current saved draft; prior versions remain in case history.
    """
    if len(provider_id) > 40 or len(timezone) > 100: raise ValueError('Invalid report options')
    return await request('POST', event_id, {'provider_id': provider_id, 'timezone': timezone}, action='/isp-report')

@mcp.tool()
async def case_jev_review(event_id: str) -> dict:
    """Request one advisory JEV review of the assigned IDS case's abstract metadata.
    No addresses, VLAN IDs, names, raw signatures or investigation notes are sent.
    Uses the operator's JEV configuration, shared daily cap and a trusted case cap.
    Repeated calls reuse the same review. Read case_get for the eventual jev_review.
    This does not start an investigation, change severity, suppress or block traffic.
    """
    return await request('POST',event_id,action='/jev-review')

@mcp.tool()
async def case_update(event_id: str, investigation_notes: str, root_cause: str,
                      status: str='escalated') -> dict:
    """Record evidence and findings on the assigned case. Use escalated when uncertain.
    resolved means the investigation is complete; recovery is tracked separately.
    Learning is queued automatically by the case API. No device configuration changes.
    """
    if status not in ('investigating','resolved','escalated'): raise ValueError('Invalid investigation status')
    if not investigation_notes.strip() or not root_cause.strip(): raise ValueError('Evidence and conclusion are required')
    if len(investigation_notes)>32000 or len(root_cause)>4000: raise ValueError('Findings exceed the case size limit')
    return await request('PATCH',event_id,{'investigation_notes':investigation_notes,'root_cause':root_cause,'status':status})

if __name__=='__main__':mcp.run()
