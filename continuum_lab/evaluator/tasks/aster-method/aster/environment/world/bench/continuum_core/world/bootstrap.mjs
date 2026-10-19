import {Game} from './engine.mjs';
import {installAudit, installPlanAudit} from './bench-instrument.mjs';
installAudit(Game);
await import('./app.mjs');
installPlanAudit();
window.__benchReady = true;
