# photo-geolocator-01 — model-free photo geolocator

Find where each of an instance's 25 street-level photos was taken, without
any model call, weight, embedding or table of photos.

## How it works

A photo carries no metadata (the grader strips it), so this entry places
each photo from what its own code reads in the pixels: the JPEG DC
coefficients. A DC coefficient is the average colour of one 8x8 block, so
decoding only the DC values gives a coarse colour map of the photo (sky,
vegetation, ground, overall warmth) with no IDCT and no model.

From that colour map the entry computes a few scene features and applies
hand-reasoned climate rules, each naming a regional anchor:

| Scene | Anchor |
|---|---|
| lush green + warm + bright | humid tropics, South / SE Asia (23, 85) |
| lush green, cooler / dimmer | temperate green, N Atlantic (7.5, 25) |
| overcast grey sky, dim | boreal north (47.5, 62.5) |
| low saturation + cool | temperate Europe (50, 10) |
| bright, dry, almost no green | arid Americas / dry subtropics (33.75, -45) |
| no green at ground level | paved / bare northern Eurasia (55.2, 40.8) |
| anything else | north-Atlantic compromise (36.25, -15) |

No network except the trial env itself; no model call; deterministic:
every answer is a function of the photo's bytes and these fixed rules.
Standard library only (Python 3.13).

## v2 calibration

The anchors are the practice-calibrated compromise points for the photo
source's own distribution (OpenStreetView-5M, which is northern-heavy),
shrunk 25% back toward the v1 hand-picked anchors so no single practice
trial's mix dominates. The no-green-ground rule is the one split that
held up under leave-one-trial-out validation on all three practice
trials: photos with essentially no green in the lower third of the frame
(paved road, bare ground, no roadside grass) sit in northern Eurasia far
more often than anywhere else. Two further splits that looked good
in-sample (very green temperate photos, very blue sky) failed the same
validation and were left out.

## Practice scores (official scorer)

| Trial | Score | Total error | Median |
|---|---|---|---|
| trial-0 | 298,077 | 202,300 km | 7,585 km |
| trial-1 | 338,736 | 161,641 km | 6,239 km |
| trial-2 | 385,062 | 115,315 km | 4,562 km |

Mean score 340,625 (v1: 312,026). Each trial answers all 25 photos in
about 13 seconds.

## Run

```
MAKEFUN_ENV_URL=<env> python3 -P -s main.py
```
