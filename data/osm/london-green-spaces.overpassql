[out:json][timeout:120];
area["boundary"="administrative"]["name"="Greater London"]["admin_level"="5"]->.london;
(
  nwr["name"]["leisure"~"^(park|garden|nature_reserve|recreation_ground)$"](area.london);
  nwr["name"]["landuse"~"^(recreation_ground|forest|meadow)$"](area.london);
  nwr["name"]["boundary"="protected_area"](area.london);
);
out tags center;
