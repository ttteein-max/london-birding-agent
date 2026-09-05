[out:json][timeout:180];
area["boundary"="administrative"]["name"="Greater London"]["admin_level"="5"]->.london;
way["name"]["leisure"~"^(park|garden|nature_reserve|recreation_ground)$"](area.london)->.greenways;
relation["name"]["leisure"~"^(park|garden|nature_reserve|recreation_ground)$"](area.london)->.greenrelations;
way(r.greenrelations)->.relationways;
(
  .greenways;
  .greenrelations;
  .relationways;
);
out body;
(
  node(w.greenways)["entrance"];
  node(w.greenways)["routing:entrance"];
  node(w.greenways)["barrier"="gate"];
  node(w.relationways)["entrance"];
  node(w.relationways)["routing:entrance"];
  node(w.relationways)["barrier"="gate"];
);
out body;
