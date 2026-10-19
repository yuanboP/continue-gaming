"""Deterministic virtual-time delayed decisions; never peeks at early answers."""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
import heapq
import json
from typing import Any


@dataclass(frozen=True)
class Action:
    move: int
    shoot: int
    focus: int
    bomb: int
    duration: int

    @property
    def keys(self) -> list[int]:
        return [self.move, self.shoot, self.focus, self.bomb]


def parse_plan(answer: Any) -> list[Action]:
    """Strict JSON single action or 1–4 action chunk, no coercion or repairs."""
    if isinstance(answer, dict) and 'answer' in answer:
        answer = answer['answer']
    if isinstance(answer, str):
        answer = json.loads(answer)
    if isinstance(answer, dict) and 'plan' in answer:
        # Optional model-reported visual estimates are audit output only.
        # The executor consumes exclusively the chosen key plan.
        answer=answer['plan']
    if not isinstance(answer, list) or not answer:
        raise ValueError('Expected [move,shoot,focus,bomb,duration] or an array of actions')
    rows = [answer] if type(answer[0]) is int else answer
    if not 1 <= len(rows) <= 4:
        raise ValueError('Plan must contain 1..4 actions')
    plan = []
    for row in rows:
        if isinstance(row,dict):
            required=('move','shoot','focus','bomb','duration')
            if set(row)!=set(required):raise ValueError('Named action fields must match frozen protocol')
            row=[row[k] for k in required]
        if not isinstance(row, list) or len(row) != 5 or any(type(v) is not int for v in row):
            raise ValueError('Actions must contain exactly five JSON integers')
        move, shoot, focus, bomb, duration = row
        if not (0 <= move <= 8 and shoot in (0, 1) and focus in (0, 1)
                and bomb in (0, 1) and 1 <= duration <= 120):
            raise ValueError('Action outside legal bounds')
        plan.append(Action(*row))
    return plan


class VirtualScheduler:
    """Capacity means simulated in-flight requests, independent of real workers.

    Call advance(frame) before taking screenshots/submitting and action_at(frame)
    before each engine tick. Every due Future is awaited regardless of actual
    wall time. A response is never available before its assigned due frame.
    Frames are experiment visual-clock frames, including cutins. Event plans
    may instead be dispatched to an environment which owns logical-duration
    execution; action_at is only for environments without their own executor.
    Plans start at due frame and replace the entire unexecuted prior suffix.
    Expiration releases movement and bomb; last shoot/focus are held. Engine
    owns X press-edge semantics, so two adjacent bomb=1 chunks do not retrigger.
    """
    def __init__(self, capacity: int = 1, first_valid_per_observation: bool = False):
        if type(capacity) is not int or capacity < 1:
            raise ValueError('capacity must be a positive integer')
        self.capacity = capacity
        self.first_valid_per_observation = first_valid_per_observation
        self.frame = 0
        self._sequence = 0
        self._heap = []
        self._terminal_heap = []
        self.latest_observation = -1
        self._latest_sequence = -1
        self._chunks = deque()
        self._held = [0, 0, 0, 0]

    @property
    def inflight(self) -> int:
        return len(self._heap) + len(self._terminal_heap)

    def available(self, frame: int | None = None) -> bool:
        return self.inflight < self.capacity

    def has_pending_observation(self, observed_frame: int) -> bool:
        """Virtual pending state; deliberately never inspect native Future.done()."""
        return any(entry[2] == observed_frame for entry in self._heap)

    @property
    def next_due_frame(self) -> int | None:
        due=[heap[0][0] for heap in (self._heap,self._terminal_heap) if heap]
        return min(due) if due else None

    def submit(self, observed_frame: int, future: Future, delay_frames: int,
               terminal_delay_frames: int | None = None) -> int | None:
        if not self.available():
            return None
        if type(observed_frame) is not int or observed_frame != self.frame:
            raise ValueError('Observation must be captured at current scheduler frame')
        if type(delay_frames) is not int or delay_frames < 0:
            raise ValueError('delay_frames must be a nonnegative integer')
        if terminal_delay_frames is not None and (type(terminal_delay_frames) is not int or terminal_delay_frames < delay_frames):
            raise ValueError('terminal_delay_frames must be an integer at least readiness delay')
        seq = self._sequence
        self._sequence += 1
        heapq.heappush(self._heap, (observed_frame + delay_frames, seq, observed_frame, future, terminal_delay_frames))
        return seq

    def advance(self, frame: int) -> list[dict]:
        if type(frame) is not int or frame < self.frame:
            raise ValueError('Virtual time cannot move backwards')
        self.frame = frame
        events = []
        while self._terminal_heap and self._terminal_heap[0][0] <= frame:
            heapq.heappop(self._terminal_heap)
        while self._heap and self._heap[0][0] <= frame:
            due, seq, observed, future, terminal_delay = heapq.heappop(self._heap)
            # Early legal actions may take effect while the original request
            # still holds a concurrency slot awaiting its terminal response.
            if terminal_delay is not None and observed + terminal_delay > frame:
                heapq.heappush(self._terminal_heap, (observed + terminal_delay, seq))
            # Even an obsolete response occupies capacity until its virtual due
            # event and its underlying real request has completed.
            try:
                result = future.result()
                if self.first_valid_per_observation and isinstance(result, dict) and result.get("invalid_action"):
                    raise ValueError("Invalid candidate action")
                plan = parse_plan(result)
            except Exception as error:
                events.append({'id': seq, 'observed_frame': observed, 'due_frame': due,
                               'status': 'invalid', 'disposition': 'invalid', 'error_type': type(error).__name__})
                continue
            if (self.first_valid_per_observation and observed <= self.latest_observation) or (observed, seq) <= (self.latest_observation, self._latest_sequence):
                events.append({'id': seq, 'observed_frame': observed, 'due_frame': due,
                               'status': 'stale', 'disposition': 'stale'})
                continue
            self.latest_observation, self._latest_sequence = observed, seq
            self._chunks.clear()
            start = due
            for action in plan:
                self._chunks.append((start, start + action.duration, action))
                start += action.duration
            events.append({'id': seq, 'observed_frame': observed, 'due_frame': due,
                           'status': 'applied', 'disposition': 'applied', 'plan': [a.keys + [a.duration] for a in plan]})
        return events

    def action_at(self, frame: int) -> list[int]:
        if frame != self.frame:
            raise ValueError('Call advance(frame) before reading the action')
        while self._chunks and self._chunks[0][1] <= frame:
            _, _, expired = self._chunks.popleft()
            self._held = [0, expired.shoot, expired.focus, 0]
        if self._chunks and self._chunks[0][0] <= frame:
            self._held = self._chunks[0][2].keys
        return self._held.copy()
