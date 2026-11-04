# Test fixtures

All fixtures are **synthetic** and exist only for deterministic, offline tests.

- `ll2_upcoming.json`, `ll2_previous.json` are shaped like The Space Devs Launch Library 2 `2.3.0`
  `/launches/upcoming/` and `/launches/previous/` responses (`mode=normal`). Every launch in them is
  invented ("Test …" missions). They are not real schedules.
- `gp_iss.json` is a CelesTrak-style OMM JSON record built from the widely published 2008 ISS
  example element set (epoch 2008-09-20). It is used with a fixed 2008 clock so element age and
  pass geometry are reproducible. Pass times for it were cross-checked against Skyfield.
