// FORK-LOCAL: the new dashboard's small adapter to module-owned Convergence.
import React, { useEffect } from 'react';
import DashboardView from '../../modules/convergence/DashboardView.jsx';

export default function DashboardConvergence(props) {
  useEffect(() => {
    if (props.preview) return;
    let terminal, disposed = false;
    const open = async event => {
      if (!event.detail?.device) return;
      try {
        terminal = await import('../panels/TerminalPanel.js');
        if (disposed) return event.detail.ack?.(false);
        await terminal.openTerminalPanel(event.detail.device);
        event.detail.ack?.(true);
      } catch { event.detail.ack?.(false); }
    };
    window.addEventListener('netclaw:open-terminal', open);
    return () => {
      disposed = true;
      window.removeEventListener('netclaw:open-terminal', open);
      terminal?.closeTerminalPanel();
    };
  }, [props.preview]);
  return <DashboardView {...props}/>;
}
