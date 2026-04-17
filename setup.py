"""
Trading Bot — Beállítási varázsló
Egyszer futtatd ezt a programot a beállítások megadásához.
Indítás: python setup.py
"""

import json
import os

SETTINGS_FILE = "user_settings.json"

TP_SZINTEK = {
    1: "TP1",
    2: "TP2",
    3: "TP3",
    4: "TP4",
    5: "TP5",
    6: "TP6",
    7: "TP7",
}

def clear():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_header():
    print("=" * 55)
    print("   Trading Bot — Személyes beállítások")
    print("=" * 55)
    print()

def kerd_int(szoveg, min_val, max_val):
    while True:
        try:
            val = int(input(szoveg))
            if min_val <= val <= max_val:
                return val
            print(f"  ⚠️  Csak {min_val}-{max_val} közötti számot adj meg!")
        except ValueError:
            print("  ⚠️  Számot adj meg!")

def kerd_float(szoveg, min_val, max_val):
    while True:
        try:
            val = float(input(szoveg).replace(',', '.'))
            if min_val <= val <= max_val:
                return val
            print(f"  ⚠️  {min_val}-{max_val} közötti értéket adj meg!")
        except ValueError:
            print("  ⚠️  Számot adj meg! (pl. 1.5)")

def kerd_igen_nem(szoveg):
    while True:
        val = input(szoveg).strip().lower()
        if val in ['i', 'igen', 'y', 'yes']:
            return True
        if val in ['n', 'nem', 'no']:
            return False
        print("  ⚠️  Írj 'i'-t (igen) vagy 'n'-t (nem)!")

def main():
    clear()
    print_header()
    print("Ez a varázsló segít beállítani a Trading Bot személyes")
    print("beállításait. A .env fájlban lévő adatokat NEM érinti.")
    print()
    input("Nyomj Enter-t a folytatáshoz...")

    settings = {}

    # ── 1. HÁNY POZÍCIÓ ───────────────────────────────────────────────────────
    clear()
    print_header()
    print("1. lépés: Hány pozíciót szeretnél futtatni?")
    print("-" * 55)
    print()
    print("  Minden pozíció külön TP célra és magic numberrel fut.")
    print("  Példa: 1 pozíció TP6-ra, vagy 3 pozíció TP3+TP5+TP6-ra.")
    print()
    print("  Maximum 5 pozíció adható meg.")
    print()

    poz_szam = kerd_int("  Pozíciók száma (1-5): ", 1, 5)
    settings["POZICIO_SZAM"] = poz_szam

    poziciok = []

    for i in range(1, poz_szam + 1):
        clear()
        print_header()
        print(f"  {i}. pozíció beállítása")
        print("-" * 55)
        print()

        # ── TP SZINT ──────────────────────────────────────────────────────────
        print("  Elérhető TP szintek:")
        for k, v in TP_SZINTEK.items():
            print(f"    {k} = {v}")
        print()
        tp_idx = kerd_int(f"  {i}. pozíció TP szintje (1-7): ", 1, 7)

        # ── MAGIC NUMBER ─────────────────────────────────────────────────────
        print()
        print("  Magic number: egyedi azonosító az MT5-ben.")
        print("  Minden pozíciónak KÜLÖNBÖZŐ magic numbernek kell lennie!")
        print("  Ajánlott: 11, 12, 13, 14, 15 ... (vagy bármilyen szám)")
        print()
        # Javasolt magic: az előző +1, vagy 10+i
        javasolt = 10 + i
        if poziciok:
            javasolt = poziciok[-1]["magic"] + 1
        magic = kerd_int(f"  {i}. pozíció magic number (javasolt: {javasolt}): ", 1, 999999)

        # Duplikáció ellenőrzés
        hasznalt_magic = [p["magic"] for p in poziciok]
        while magic in hasznalt_magic:
            print(f"  ⚠️  A {magic} magic number már használatban van! Adj meg másikat.")
            magic = kerd_int(f"  {i}. pozíció magic number: ", 1, 999999)

        poziciok.append({
            "tp_index": tp_idx,
            "magic": magic,
            "label": f"TP{tp_idx}-fix",
        })

        print()
        print(f"  ✅ {i}. pozíció: TP{tp_idx} | Magic: {magic}")
        input("  Nyomj Enter-t a folytatáshoz...")

    settings["POZICIOK"] = poziciok

    # ── 2. LOT MÉRETEZÉS ─────────────────────────────────────────────────────
    clear()
    print_header()
    print("2. lépés: Lot méretezés")
    print("-" * 55)
    print()
    print("  Automata: a bot kiszámolja a lot méretet a számlaegyenleg")
    print("            és a megadott kockázat % alapján.")
    print()
    print("  Manuális: te adod meg a fix lot méretet minden pozícióhoz.")
    print()

    auto_lot = kerd_igen_nem("  Automata lot méretezést szeretnél? (i/n): ")
    settings["AUTO_LOT"] = auto_lot

    if auto_lot:
        clear()
        print_header()
        print("2a. lépés: Kockázat mértéke")
        print("-" * 55)
        print()
        print("  Példa 1000 USD egyenlegnél:")
        print("    0.5% → max 5 USD veszteség / kereskedés")
        print("    1.0% → max 10 USD veszteség / kereskedés")
        print()
        print("  Ajánlott: 0.5% - 2%")
        print()
        for p in poziciok:
            kock = kerd_float(f"  TP{p['tp_index']} (magic={p['magic']}) kockázat % (pl. 1.0): ", 0.1, 10.0)
            p["risk_pct"] = kock
            p["lot"] = None
    else:
        clear()
        print_header()
        print("2b. lépés: Manuális lot méretek")
        print("-" * 55)
        print()
        print("  Add meg a fix lot méretet minden pozícióhoz.")
        print("  (0.01 = mini lot)")
        print()
        for p in poziciok:
            lot = kerd_float(f"  TP{p['tp_index']} (magic={p['magic']}) lot méret (pl. 0.01): ", 0.01, 100.0)
            p["lot"] = lot
            p["risk_pct"] = None

    # ── 3. MOZGÓ SL ───────────────────────────────────────────────────────────
    clear()
    print_header()
    print("3. lépés: Mozgó Stop Loss")
    print("-" * 55)
    print()
    print("  Fix SL:   az SL végig az eredeti szinten marad.")
    print()
    print("  Mozgó SL: az SL automatikusan lép fel ahogy az ár")
    print("            eléri a TP szinteket.")
    print()
    print("  Figyelem: ha a csatornád max TP3-ig megy, érdemes")
    print("  kikapcsolni a mozgó SL-t, mert nincs elég mozgástér!")
    print()

    mozgo_sl = kerd_igen_nem("  Mozgó SL-t szeretnél? (i/n): ")
    settings["MOZGO_SL_ENABLED"] = mozgo_sl

    if mozgo_sl:
        clear()
        print_header()
        print("3a. lépés: Mozgó SL — melyik TP-nél mozduljon először?")
        print("-" * 55)
        print()
        print("  Ez azt jelenti, hogy melyik TP elérése után kerüljön")
        print("  az SL az entry szintre (break-even).")
        print()
        print("  Példa ha SL_MOZGAS_ELSO_TP = 3:")
        print("    TP3 elérve → SL = Entry (break-even)")
        print("    TP4 elérve → SL = TP1")
        print("    TP5 elérve → SL = TP2")
        print("    stb.")
        print()
        print("  Ha csak TP3-ig megy a pozíció és ezt 3-ra állítod,")
        print("  az SL NEM mozdul (mert TP3 = a pozíció zárul).")
        print("  Ilyenkor válassz kisebb számot (pl. 1 vagy 2).")
        print()
        sl_elso_tp = kerd_int("  SL először mozdul TP szintnél (1-9): ", 1, 9)
        settings["SL_MOZGAS_ELSO_TP"] = sl_elso_tp
    else:
        settings["SL_MOZGAS_ELSO_TP"] = 3  # alapértelmezett, nem használt

    # ── 4. NAPI VESZTESÉG LIMIT ───────────────────────────────────────────────
    clear()
    print_header()
    print("4. lépés: Napi veszteség limit")
    print("-" * 55)
    print()
    print("  Ha a nap folyamán a bot ennyit veszít, automatikusan")
    print("  leáll és másnap reggel újraindul.")
    print()
    print("  0 = kikapcsolva")
    print()

    napi_limit = kerd_igen_nem("  Szeretnél napi veszteség limitet? (i/n): ")
    if napi_limit:
        napi_pct = kerd_float("  Max napi veszteség % (pl. 5.0): ", 0.1, 50.0)
        settings["DAILY_LOSS_LIMIT_PCT"] = napi_pct
    else:
        settings["DAILY_LOSS_LIMIT_PCT"] = 0.0

    # ── 5. MAX NAPI KERESKEDÉS ────────────────────────────────────────────────
    clear()
    print_header()
    print("5. lépés: Napi maximum kereskedések száma")
    print("-" * 55)
    print()
    print("  0 = korlátlan")
    print("  1 = maximum 1 jelzés naponta (ajánlott)")
    print()

    max_napi = kerd_int("  Max napi kereskedés (0=korlátlan): ", 0, 10)
    settings["MAX_NAPI_KERESKEDES"] = max_napi

    # Időablak alapértékek
    settings["TRADE_HOURS_ENABLED"] = False
    settings["TRADE_HOUR_START"]    = 0
    settings["TRADE_HOUR_END"]      = 24

    # ── ÖSSZEFOGLALÁS ─────────────────────────────────────────────────────────
    clear()
    print_header()
    print("Összefoglalás — kérlek ellenőrizd!")
    print("-" * 55)
    print()
    print(f"  Pozíciók száma: {poz_szam}")
    print()
    for i, p in enumerate(poziciok, 1):
        lot_info = f"{p['lot']} lot" if p.get("lot") else f"{p.get('risk_pct')}% kockázat"
        print(f"  {i}. pozíció: TP{p['tp_index']} | Magic: {p['magic']} | {lot_info}")

    print()
    print(f"  Mozgó SL:         {'igen' if mozgo_sl else 'nem'}")
    if settings["DAILY_LOSS_LIMIT_PCT"] > 0:
        print(f"  Napi limit:       {settings['DAILY_LOSS_LIMIT_PCT']}%")
    else:
        print(f"  Napi limit:       kikapcsolva")
    max_k = settings.get('MAX_NAPI_KERESKEDES', 0)
    print(f"  Max napi keresk.: {'korlátlan' if max_k == 0 else str(max_k)}")
    print()

    mentes = kerd_igen_nem("  Elmented a beállításokat? (i/n): ")
    if mentes:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        print()
        print("  ✅ Beállítások elmentve!")
        print(f"  Fájl: {os.path.abspath(SETTINGS_FILE)}")
        print()
        print("  Most már elindíthatod a botot a main1.bat-tal.")
    else:
        print()
        print("  ❌ Mentés megszakítva — semmi nem változott.")

    print()
    input("  Nyomj Enter-t a kilépéshez...")

if __name__ == "__main__":
    main()
