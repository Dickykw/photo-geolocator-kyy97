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
| overcast grey sky, dim | boreal north, Siberia (57.5, 72.5) |
| low saturation + cool | temperate Europe (50, 10) |
| bright, dry, almost no green | arid Americas (23.75, -75) |
| no green at ground level | paved / bare northern Eurasia (55.2, 40.8) |
| vivid-green cells, otherwise fallback | tropical Indian Ocean / SE Asia (10.8, 92.6) |
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

## v3 changes

Every change below improves all three practice trials, pooled and under
leave-one-trial-out validation:

- Arid anchor moved south-west to (23.75, -75): the arid leaf's truths
  sit in the Americas (median 16.3, -70.6).
- Boreal anchor moved to (57.5, 72.5): the boreal leaf's truths sit
  further north and east (Siberia / far-east Russia).
- New vivid-green rule: fallback photos that still carry a small share
  of vivid-green cells at full block resolution (g > r+20 and g > b+20
  and g > 90, share > 0.0017) are tropical — their practice median is
  (10.8, 92.6), Indian Ocean / SE Asia — so they get that anchor
  instead of the north-Atlantic compromise. The threshold sits mid-gap
  between the highest non-member (0.0012) and the lowest member (0.0022).

Rejected in v3: a vertical edge-density split (failed
leave-one-trial-out on trial-1), continuous per-leaf regression
(overfits badly), and every hemisphere rule tried (the apparent signal
was a trial-mix confound, not a hemisphere signal).

## Practice scores (official scorer)

| Trial | Score | Total error | Median |
|---|---|---|---|
| trial-0 | 305,999 | 194,378 km | 7,427 km |
| trial-1 | 343,079 | 157,298 km | 5,873 km |
| trial-2 | 395,342 | 105,035 km | 3,387 km |

Mean score 348,140 (v2: 340,625; v1: 312,026). Each trial answers all
25 photos in about 13 seconds.

## Run

```
MAKEFUN_ENV_URL=<env> python3 -P -s main.py
```
