"""PIEZA 2 – Procesador. Lee procesador/fuentes.json, corre etl_capas.py por fuente
y deja los GeoJSON + manifest.json en docs/capas/ (los lee la PIEZA 1, el dashboard).
Ejecutar desde la raíz del repositorio:  python procesador/procesar.py"""
import json, shutil, subprocess, sys
from pathlib import Path

aqui = Path(__file__).parent
cfg = json.load(open(aqui / "fuentes.json", encoding="utf-8"))
docs = Path("docs")
out = docs / "capas"
shutil.rmtree(out, ignore_errors=True)
out.mkdir(parents=True)
items, limites = [], None

for i, f in enumerate(cfg["fuentes"]):
    if not Path(f["fuente"]).exists():
        print("⚠ no existe, se omite:", f["fuente"])
        continue
    sub = out / f"{i:02d}"
    cmd = [sys.executable, str(aqui / "etl_capas.py"), "--fuente", f["fuente"], "--limites", cfg["limites"],
           "--dep", cfg.get("dep", "CAJAMARCA"), "--salida", str(sub)]
    if f.get("capas"):
        cmd += ["--capas", *f["capas"]]
    if f.get("region"):
        cmd += ["--region", f["region"]]
    subprocess.run(cmd, check=True)
    for g in sorted(sub.glob("*.geojson")):
        if g.name.startswith("limites_"):
            if limites is None:
                limites = out / "limites.geojson"
                shutil.copy(g, limites)
            g.unlink()
            continue
        it = {"url": g.relative_to(docs).as_posix(), "name": g.stem, "role": f["role"]}
        if f["role"] == "riesgo":
            it.update(ent=f["ent"], comp=f["comp"])
        items.append(it)

if limites:
    items.insert(0, {"url": limites.relative_to(docs).as_posix(), "name": "Límites distritales", "role": "limite"})
json.dump({"items": items}, open(out / "manifest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"✅ manifest con {len(items)} capas")
