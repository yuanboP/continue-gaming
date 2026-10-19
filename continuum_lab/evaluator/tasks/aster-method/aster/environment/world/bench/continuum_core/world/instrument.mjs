// Trusted instrumentation, loaded before the unchanged native app module.
// It observes Game.step and RAF; it never chooses, retimes, or edits actions.
export function installAudit(Game, target = globalThis) {
  const rawStep = Game.prototype.step;
  const rawReset = Game.prototype.reset;
  const originalRAF = target.requestAnimationFrame.bind(target);
  const clone = value => JSON.parse(JSON.stringify(value));
  let game = null;
  const audit = {
    active: false, sealed: false, initial: null, final: null,
    ticks: [], raf: [], applications: [], resets: [], anomalies: [],
    started_at_ms: null, ended_at_ms: null, initial_clock: null, final_clock: null,
    request_id: null, run_kind: null, last_raf_ms: null,
    begin(runKind) {
      this.active = true; this.sealed = false; this.initial = null; this.final = null;
      this.ticks = []; this.raf = []; this.applications = []; this.resets = [];
      this.anomalies = []; this.started_at_ms = target.performance.now();
      this.ended_at_ms = null; this.initial_clock = null; this.final_clock = null;
      this.request_id = null; this.run_kind = runKind; this.last_raf_ms = null;
    },
    initialized() {
      this.initial = clone(game.snapshot());
      this.initial_clock = clock();
    },
    sealIfTerminal() {
      if (this.active && !this.sealed && game && game.status !== 'playing') this.seal();
    },
    seal(reason = null) {
      if (this.sealed || !this.active) return;
      if (reason) this.anomalies.push({kind:'aborted', reason:String(reason)});
      this.final = clone(game.snapshot());
      this.final_clock = clock();
      this.ended_at_ms = target.performance.now();
      this.sealed = true;
    },
    snapshot() {
      this.sealIfTerminal();
      return clone({initial:this.initial, final:this.final, ticks:this.ticks,
        raf:this.raf, applications:this.applications, resets:this.resets,
        anomalies:this.anomalies, started_at_ms:this.started_at_ms,
        ended_at_ms:this.ended_at_ms, initial_clock:this.initial_clock,
        final_clock:this.final_clock, run_kind:this.run_kind, sealed:this.sealed});
    },
  };
  function clock() {
    const c = target.research?.getClock?.();
    if (!c) return null;
    return clone(c);
  }
  function summary(g) {
    return {tick:g.tick,status:g.status,lives:g.player.lives,bombs:g.player.bombs,
      hp:g.boss.hp,score:g.score,graze:g.graze,hits:g.hits};
  }
  Game.prototype.reset = function(seed) {
    const result = rawReset.call(this, seed);
    game = this;
    if (audit.active && !audit.sealed) audit.resets.push({seed:this.seed, tick:this.tick,
      at_ms:target.performance.now()});
    return result;
  };
  Game.prototype.step = function(action = [0,0,0,0]) {
    game = this;
    const recording = audit.active && !audit.sealed;
    const before = this.tick;
    const at = target.performance.now();
    const result = rawStep.call(this, action);
    if (recording) {
      audit.ticks.push({index:audit.ticks.length, tick_before:before,
        action:[...action], at_ms:at, after:summary(this), result:clone(result)});
      if (this.tick !== before + 1) audit.anomalies.push({kind:'nonadvancing_step',tick:before});
    }
    return result;
  };
  target.requestAnimationFrame = callback => originalRAF(timestamp => {
    const recording = audit.active && !audit.sealed;
    const entered = target.performance.now();
    try { return callback(timestamp); }
    finally {
      if (recording) {
        const c = clock();
        const gap = audit.last_raf_ms === null ? entered-audit.started_at_ms : entered-audit.last_raf_ms;
        audit.last_raf_ms = entered;
        audit.raf.push({index:audit.raf.length, timestamp_ms:timestamp,
          entered_ms:entered, completed_ms:target.performance.now(), gap_ms:gap,
          frame:c?.frame,visual_frame:c?.visual_frame,intro:c?.intro,
          mode:c?.mode,speed:c?.speed,paused:c?.paused,blur_events:c?.blur_events,
          status:c?.status,wall_ms:c?.wall_ms,max_raf_gap_ms:c?.max_raf_gap_ms});
        audit.sealIfTerminal();
      }
    }
  });
  target.__benchAudit = audit;
  return audit;
}

export function installPlanAudit(target = globalThis) {
  const original = target.research.apply.bind(target.research);
  target.research.apply = function(plan) {
    const audit = target.__benchAudit;
    const before = target.research.getClock();
    const entered = target.performance.now();
    const result = original(plan);
    if (audit.active && !audit.sealed) audit.applications.push({
      index:audit.applications.length,request_id:audit.request_id,
      plan:JSON.parse(JSON.stringify(plan)),entered_ms:entered,
      completed_ms:target.performance.now(),before,after:result,
    });
    return result;
  };
}
