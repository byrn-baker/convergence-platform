import React, { useEffect, useRef, useState } from 'react';
import { HomeView } from './HomeView.js';
import './dashboard.css';

/** Inline workspace: shares the classic controller/API, with dashboard-owned chrome. */
export default function DashboardView({ preview, active, refreshKey, onStatus, investigate, hostAccess }) {
  const host = useRef(null), controller = useRef(null);
  const statusCallback = useRef(onStatus);
  statusCallback.current = onStatus;
  const [snapshot, setSnapshot] = useState({ label: 'Connecting to Convergence' });

  useEffect(() => {
    if (preview) { statusCallback.current?.('Preview · no live operations'); return; }
    const view = new HomeView(host.current, {
      embedded: true, hostAccess,
      investigate,
      onUpdate(current) {
        const key = { 'vm-security': 'vmSecurity', overview: 'health', diary: 'events', triage: 'escalated', isp: 'wanCases', flows: 'flows' }[current.subview] || current.subview;
        const unavailable = current.lastError || current.sourceErrors[current.subview] || current.cache[key]?.unavailable || current.cache[key]?.status === 'unavailable' || current.cache[key]?.error
          || (current.subview === 'security' && current.cache.securityCases?.unavailable);
        const label = unavailable ? 'Convergence unavailable' : current.loading ? 'Refreshing Convergence' : current.lastSuccess ? 'Convergence available' : 'Connecting to Convergence';
        setSnapshot({ label, read: current.readTimes[key], site: current.site, stale: Boolean(unavailable), canInspect: key !== 'models' && Boolean(current.cache[key]) });
        statusCallback.current?.(label);
      },
    });
    controller.current = view;
    view.mount();
    return () => { controller.current = null; view.destroy(); };
  }, [preview, hostAccess]);

  useEffect(() => {
    if (!preview && active && refreshKey) controller.current?.refresh(true);
  }, [refreshKey, preview]);

  useEffect(() => {
    if (preview || !active) return;
    const timer = setInterval(() => {
      const view = controller.current;
      // Section buttons stay mounted when the content refreshes. Leaving one
      // focused after navigation must not freeze recovered telemetry. Keep
      // protecting focus and drafts in the content and site picker.
      const contentHasFocus = host.current?.contains(document.activeElement)
        && !document.activeElement?.matches('[data-home-sub]');
      if (document.hidden || contentHasFocus || view?.triageBusy || view?.modelsBusy || view?.incidentPending?.size) return;
      view?.refresh();
    }, 30000);
    return () => clearInterval(timer);
  }, [preview, active]);

  const inspect = () => {
    const view = controller.current;
    if (!view) return;
    const key = { 'vm-security': 'vmSecurity', overview: 'health', wifi: 'wifi', devices: 'devices', security: 'security', diary: 'events', triage: 'escalated', isp: 'wanCases', flows: 'flows' }[view.subview];
    if (!key) return;
    investigate(`Convergence · ${view.site} · ${view.subview}`, { site: view.site, section: view.subview, data: view.cache[key], readError: view.lastError?.message }, `/api/home/${{ vmSecurity: 'security/vms', events: 'events', escalated: 'events/escalated', wanCases: 'events?type=wan' }[key] || key}`, view.readTimes[key]);
  };

  return <section className="convergence-workspace" hidden={!active} aria-label="Convergence network operations">
    {preview ? <div className="panel"><h2>Convergence · Network Guardian</h2><p>Site health, Wi-Fi, devices, investigation diary, operator triage and model controls are available in the live HUD. Live reads, device consoles and changes are disabled in this synthetic preview.</p></div> : <>
      <div ref={host}/>
      <div className="convergence-source"><p role="status"><strong>{snapshot.label}</strong><span>Site · {snapshot.site || 'Discovering'}</span><span>{snapshot.read ? `Last successful read · ${new Date(snapshot.read).toLocaleString()}${snapshot.stale ? ' · showing previous observations' : ''}` : 'No successful read yet'}</span></p><button onClick={inspect} disabled={!snapshot.read || !snapshot.canInspect}>Investigate in Canvas ↗</button></div>
    </>}
  </section>;
}
