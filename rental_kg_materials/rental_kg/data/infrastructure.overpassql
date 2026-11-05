// Proposed collection area, not a completed extraction.
// South, west, north, east. Includes a buffer around the intended pilot area.
[out:json][timeout:60];
(
 nwr[railway=station][station=subway](59.88,30.20,60.02,30.50);
 nwr[shop=supermarket](59.88,30.20,60.02,30.50);
 nwr[leisure=park](59.88,30.20,60.02,30.50);
);
out center tags;
