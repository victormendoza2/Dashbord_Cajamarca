#!/usr/bin/env python3
"""
ETL Dashboard de Riesgos – Cajamarca
Convierte GDB (ArcGIS) / GPKG / SHP / ZIP en GeoJSON ligeros, con topología corregida
y recortados por ámbito, listos para cargar en la pestaña "Capas y fuentes".

Requisitos:  pip install geopandas shapely pyogrio

Ejemplos
  # CENEPRED (GDB o GPKG, campo GRIDCODE) recortado al departamento
  python etl_capas.py --fuente CENEPRED.gdb --limites distrito.gpkg --dep CAJAMARCA --salida capas/

  # SENAMHI (ZIP descargado de la web, campo nivel), solo una capa y por provincia
  python etl_capas.py --fuente aviso_24h.zip --limites distrito.gpkg --dep CAJAMARCA --prov CAJAMARCA --salida capas/

  # Elementos expuestos del GORE (colegios, salud, vías, parcelas), sin recorte de riesgo
  python etl_capas.py --fuente gore_capas.gdb --limites distrito.gpkg --dep CAJAMARCA --salida capas/ --gpkg
"""
import argparse, re, tempfile, unicodedata, zipfile
from pathlib import Path
import geopandas as gpd
import pyogrio
from shapely.validation import make_valid
from shapely.ops import unary_union

POLI = ("Polygon", "MultiPolygon")


def slug(t):
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")


def resolver_fuente(ruta):
    """ZIP -> descomprime y devuelve .gdb/.gpkg/.shp; el resto se usa tal cual."""
    p = Path(ruta)
    if p.suffix.lower() != ".zip":
        return p
    tmp = Path(tempfile.mkdtemp(prefix="etl_"))
    with zipfile.ZipFile(p) as z:
        z.extractall(tmp)
    for patron in ("**/*.gdb", "**/*.gpkg", "**/*.shp"):
        hall = sorted(tmp.glob(patron))
        if hall:
            return hall[0]
    raise SystemExit("El ZIP no contiene .gdb, .gpkg ni .shp")


def listar_capas(fuente):
    if fuente.suffix.lower() == ".shp":
        return [fuente.stem]
    return [c[0] for c in pyogrio.list_layers(fuente)]


def solo_poligonos(g):
    if g is None or g.is_empty:
        return None
    if g.geom_type in POLI:
        return g
    if g.geom_type == "GeometryCollection":
        ps = [x for x in g.geoms if x.geom_type in POLI]
        return unary_union(ps) if ps else None
    return None


def reparar(gdf):
    """CRS -> EPSG:4326, geometrías válidas, sin vacíos, sin multipartes colapsadas."""
    if gdf.crs is None:
        print("   ⚠ capa sin CRS; se asume EPSG:4326")
        gdf = gdf.set_crs(4326)
    gdf = gdf.to_crs(4326)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf["geometry"] = gdf.geometry.apply(make_valid)
    es_poli = gdf.geom_type.isin(POLI + ("GeometryCollection",)).any()
    if es_poli:
        gdf["geometry"] = gdf.geometry.apply(solo_poligonos)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    return gdf.explode(index_parts=False).reset_index(drop=True)


def cargar_ambito(ruta, dep, prov, dist, tol=0):
    lim = gpd.read_file(ruta).to_crs(4326)
    ren = {"NOMBDEP": "DEPARTAMEN", "NOMBPROV": "PROVINCIA", "NOMBDIST": "DISTRITO"}
    lim = lim.rename(columns=ren)
    for col, val in (("DEPARTAMEN", dep), ("PROVINCIA", prov), ("DISTRITO", dist)):
        if val:
            lim = lim[lim[col].str.upper() == val.upper()]
    if lim.empty:
        raise SystemExit("El ámbito indicado no existe en la capa de límites")
    lim["geometry"] = lim.geometry.apply(make_valid)
    if tol:
        lim["geometry"] = lim.geometry.simplify(tol, preserve_topology=True)
    return lim[["DEPARTAMEN", "PROVINCIA", "DISTRITO", "geometry"]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fuente", required=True, help="GDB, GPKG, SHP o ZIP")
    ap.add_argument("--limites", help="Capa distrital (NOMBDEP/NOMBPROV/NOMBDIST)")
    ap.add_argument("--listar", action="store_true", help="Solo lista las capas de la fuente")
    ap.add_argument("--dep", default="CAJAMARCA")
    ap.add_argument("--prov")
    ap.add_argument("--dist")
    ap.add_argument("--capas", nargs="*", help="Capas a procesar (por defecto todas)")
    ap.add_argument("--region", help="Filtra por campo 'region' si existe (capas CENEPRED)")
    ap.add_argument("--tol", type=float, default=0.0001, help="Simplificación en grados (~11 m)")
    ap.add_argument("--salida", default="capas")
    ap.add_argument("--gpkg", action="store_true", help="Además, un GPKG con todas las capas")
    a = ap.parse_args()

    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    fuente = resolver_fuente(a.fuente)
    if a.listar:
        print("\n".join(listar_capas(fuente)))
        return
    if not a.limites:
        raise SystemExit("Falta --limites")
    lim = cargar_ambito(a.limites, a.dep, a.prov, a.dist, a.tol)
    mask = unary_union(lim.geometry)
    lim.to_file(out / f"limites_{slug(a.dist or a.prov or a.dep)}.geojson", driver="GeoJSON")
    print(f"✅ límites: {len(lim)} distritos")

    for nombre in (a.capas or listar_capas(fuente)):
        print(f"→ {nombre}")
        g = gpd.read_file(fuente, layer=nombre) if fuente.suffix.lower() != ".shp" else gpd.read_file(fuente)
        if a.region and "region" in g.columns:
            g = g[g["region"].astype(str).str.upper() == a.region.upper()]
        g = reparar(g)
        g = gpd.clip(g, mask)                      # recorte vectorial por ámbito
        g = reparar(g)                             # el clip puede generar geometrías inválidas
        if a.tol and g.geom_type.isin(POLI).any():
            g["geometry"] = g.geometry.simplify(a.tol, preserve_topology=True)
            g = g[~g.geometry.is_empty]
        if g.empty:
            print("   (sin datos en el ámbito)")
            continue
        for c in g.columns:                        # GeoJSON no admite fechas nativas
            if str(g[c].dtype).startswith("datetime"):
                g[c] = g[c].astype(str)
        dest = out / f"{slug(nombre)}_{slug(a.dist or a.prov or a.dep)}.geojson"
        g.to_file(dest, driver="GeoJSON", COORDINATE_PRECISION=6)
        if a.gpkg:
            g.to_file(out / f"capas_{slug(a.dep)}.gpkg", layer=slug(nombre), driver="GPKG")
        print(f"   {len(g)} entidades → {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
