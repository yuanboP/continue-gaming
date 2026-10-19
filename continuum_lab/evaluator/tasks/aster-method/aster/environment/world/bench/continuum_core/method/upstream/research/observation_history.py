"""Past screenshot selection only: no entity state, actions, or future data."""
from collections import deque


def positive_frames(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(name + ' must be a positive integer')
    return value


class ObservationHistory:
    """Keep fresh captures separately from the actor's spaced image history.

    With neither new option supplied, retain the original last-N deque exactly.
    Opt-in captures replace duplicate logical frames, so intro animation cannot
    become several apparent motion samples at the same physics timestamp.
    """
    def __init__(self, config):
        self.count=config.get('history_frames',3)
        if type(self.count) is not int or not 1 <= self.count <= 3:
            raise ValueError('history_frames must be an integer in1..3')
        self.legacy='capture_stride' not in config and 'history_spacing_frames' not in config
        self.spacing=config.get('history_spacing_frames')
        if 'history_spacing_frames' in config:
            positive_frames(self.spacing,'history_spacing_frames')
        self.capture_stride=config.get('capture_stride',config.get('image_stride',config.get('decision_stride',15)))
        self.explicit_capture_stride='capture_stride' in config
        if self.explicit_capture_stride:
            positive_frames(self.capture_stride,'capture_stride')
        self._samples=deque(maxlen=self.count if self.spacing is None else None)

    def append(self, frame, png):
        if not self.legacy:
            if type(frame) is not int or frame < 0:
                raise ValueError('Observed logical frame must be a nonnegative integer')
            if self._samples and frame < self._samples[-1][0]:
                raise ValueError('Observed logical clock moved backwards')
            if self._samples and frame == self._samples[-1][0]:
                self._samples[-1]=(frame,png)
                return
        self._samples.append((frame,png))
        if self.spacing is not None:
            oldest_target=frame-(self.count-1)*self.spacing
            # Retain one bracketing sample at/before the oldest target, plus
            # the newer samples needed when the latest capture advances.
            while len(self._samples)>1 and self._samples[1][0]<=oldest_target:
                self._samples.popleft()

    def select(self):
        if not self._samples:
            return []
        if self.spacing is None:
            return list(self._samples)
        latest=self._samples[-1]
        selected=[latest];used={latest[0]}
        for index in range(1,self.count):
            target=latest[0]-index*self.spacing
            older=next((sample for sample in reversed(self._samples) if sample[0]<=target),None)
            if older is not None and older[0] not in used:
                selected.append(older);used.add(older[0])
        return list(reversed(selected))

    def next_capture_after(self, loop_visual_frame, observation):
        # In realtime an atomic capture may be newer than the loop clock. The
        # override uses its actual timestamp; legacy keeps its old cadence.
        anchor=observation.get('clock',{}).get('visual_frame',loop_visual_frame) if self.explicit_capture_stride else loop_visual_frame
        return anchor+self.capture_stride
