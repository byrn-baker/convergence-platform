import React from 'react';
import DashboardView from '../../modules/convergence/DashboardView.jsx';
import { convergenceAccess } from './convergence-access.js';

export default function DashboardConvergence(props) {
  return <DashboardView {...props} hostAccess={convergenceAccess}/>;
}
