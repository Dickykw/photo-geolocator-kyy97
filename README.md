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
| lush green + warm + bright | humid tropics, SE Asia (8, 100) |
| lush green, cooler / dimmer | equatorial, Africa / N South America (0, 25) |
| overcast grey sky, dim | boreal north, Russia / N Europe (55, 40) |
| low saturation + cool | temperate Europe (50, 10) |
| bright, dry, almost no green | arid, Middle East / N Africa (30, 45) |
| anything else | the populated world's centre (25, 0) |

No network except the trial env itself; no model call; deterministic:
every answer is a function of the photo's bytes and these fixed rules.
Standard library only (Python 3.13).

## Practice scores (official scorer)

| Trial | Score | Total error |
|---|---|---|
| trial-0 | 281,793 | 218,584 km |
| trial-1 | 313,807 | 186,570 km |
| trial-2 | 340,477 | 159,900 km |

Each trial answers all 25 photos in about 9 seconds.

## Run

```
MAKEFUN_ENV_URL=<env> python3 -P -s main.py
```
