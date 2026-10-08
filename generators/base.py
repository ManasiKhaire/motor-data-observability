"""The loop every generator shares: build a batch, apply any injected problem, write it.

A subclass only has to define:
  name        the source name, e.g. "claims"
  problems    the failures it can inject, as {name: description}
  make_batch(n, problem) -> list of records, or None to write nothing this time
"""
import random
import time
from pathlib import Path

from common.io import get_injection, log_event, set_injection, write_batch
from common.reference import Reference


class BaseGenerator:
    name = "base"
    problems: dict = {}

    def __init__(self, settings: dict, ref: Reference, out_dir: Path):
        self.settings = settings
        self.cfg = settings["sources"][self.name]
        self.ref = ref
        self.out_dir = Path(out_dir)
        # Different seed per source, so sources don't produce identical random streams.
        self.rng = random.Random(f"{settings['seed']}-{self.name}-{time.time_ns()}")
        self.batch_no = 0
        self._last_problem = None

    # Subclasses override this.
    def make_batch(self, n: int, problem):
        raise NotImplementedError

    def log(self, level: str, message: str, **extra):
        log_event(self.out_dir, self.name, level, message, **extra)

    def current_problem(self, override=None):
        problem = override or get_injection(self.out_dir, self.name)
        if problem and problem not in self.problems:
            self.log("WARN", f"Unknown problem '{problem}' ignored", valid=list(self.problems))
            return None
        if problem != self._last_problem:
            if problem:
                self.log("INFO", f"Problem injection switched on: {problem}", problem=problem)
            elif self._last_problem:
                self.log("INFO", f"Problem injection switched off: {self._last_problem}")
            self._last_problem = problem
        return problem

    def step(self, problem=None):
        """Run one batch. Returns the written file path, or None."""
        problem = self.current_problem(problem)
        self.batch_no += 1
        records = self.make_batch(self.cfg["records_per_batch"], problem)
        if not records:
            return None
        path = write_batch(self.out_dir, self.name, records, self.cfg["format"], self.batch_no)
        self.log("INFO", "Batch written", file=path.name, rows=len(records), problem=problem)
        return path

    def run(self, batches: int = 0, inject: str = None, interval: float = None):
        """Loop forever (batches=0) or for a fixed number of batches."""
        if inject:
            set_injection(self.out_dir, self.name, inject)
        wait = interval if interval is not None else self.cfg["interval_seconds"]
        print(f"[{self.name}] writing every {wait}s to {self.out_dir / 'landing' / self.name}  (Ctrl+C to stop)")
        done = 0
        try:
            while batches == 0 or done < batches:
                path = self.step()
                problem = self._last_problem
                status = path.name if path else "no file this batch"
                print(f"[{self.name}] batch {self.batch_no}: {status}" + (f"  <-- {problem}" if problem else ""))
                done += 1
                if batches == 0 or done < batches:
                    time.sleep(wait)
        except NotImplementedError as e:
            print(f"[{self.name}] not written yet: {e}")
        except KeyboardInterrupt:
            print(f"\n[{self.name}] stopped")
