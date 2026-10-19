"""Selected v2 observation gates; all actions remain model-authored."""
def validate_known_plan_feedback(config):
    if config.get("known_plan_feedback") or config.get("rolling_observations"):
        raise ValueError("Selected v2 uses winner and movement gated observations")
    return False

def observation_gate_open(config, clock, capacity_available, lastissued, latest_observation, pending):
    """Existing runner gates, shared with offline protocol invariant tests."""
    available = capacity_available
    if config.get('wait_for_winner') and lastissued >= 0 and (latest_observation < lastissued):
        if pending(lastissued):
            available = False
    if config.get('wait_for_move') and (clock['held'][0] != 0 or any((a[0] != 0 for a in clock.get('pending_plan', [])))):
        available = False
    return available
