#!/usr/bin/env bash
#
# seed-kelly-data.sh — recreate the Albinati profile + active trip dossiers
# on a fresh Kelly install (e.g. the Hetzner box). Generated 2026-06-17 from
# the dev SQLite. Mirrors the data via the CLI — no DB copy needed.
#
# RUN ONCE: session-create always makes a NEW dossier, so re-running this
# duplicates the sessions (the profile is an idempotent upsert, safe to repeat).
# Override the binary with KELLY=/abs/path/kelly if it's not on PATH.
#
set -euo pipefail
KELLY="${KELLY:-kelly}"
idof() { python3 -c "import sys,json;print(json.load(sys.stdin)[\"id\"])"; }

echo ">> profile: albinati"
"$KELLY" profile-set albinati 'Albinati family' \
  --payload-json '{"travellers":{"adults":["Luis","Karol"],"children":[{"name":"Theo","band":"child_<=11","in_school":"London state school — trips MUST avoid term time"},{"name":"(2nd child)","band":"child_<=11"}],"pax_family":4},"home_origin_airports":["LHR","LGW","LCY","STN","LTN"],"eurostar_origin":"London St Pancras","mode_pref":"Avios first","prefs":{"food_first":"Karol is a nutritionist; no red meat","style":"boutique hotels for couple trips; family trips kid-friendly","relax_over_checklist":true},"hard_constraints":["family trips pinned to London school holidays (Theo)","verify Theo'"'"'s specific school calendar each time"],"school_calendar":{"feb_half_term_2027":"Mon 15 - Fri 19 Feb (window Sat 13 - Sun 21)","oct_half_term_2026":"Mon 26 - Fri 30 Oct (window Sat 24 Oct - Sun 1 Nov)"},"already_done":["Tenerife (winter sun)","Chamonix (ski)"],"couple_getaways":"kid-free escapes are food-first, boutique, Avios, relax > checklist","active_sessions":[{"id":1,"what":"Feb-2027 half-term winter sun (Avios) — Lanzarote frontrunner; return award space is bottleneck"},{"id":2,"what":"Oct-2026 half-term city/aurora (Avios) — Copenhagen bookable now (32k Avios/4pax); aurora not bookable on Avios"}]}'

echo ">> session: Half-term out/2026 — cidade/aurora em Avios, 3-4 noites (4 p"
sid=$("$KELLY" session-create 'Half-term out/2026 — cidade/aurora em Avios, 3-4 noites (4 pax)' \
  --destination 'Copenhague (CPH) FRONTRUNNER — fecha em Avios agora; Munique (MUC) só meio de semana; aurora (Tromsø/Reykjavik) não fecha em Avios' \
  --notes 'PIVÔ: sol -> cidade/aurora. Cidades têm assento-prêmio farto nos 2 sentidos (≠ ilhas). CPH 4000 Avios/trecho, ida sáb24/out + volta ter27/qua28, 9 lugares cada = 32000 Avios RT/4pax + taxas; Tivoli Halloween. MUC só midweek (sem fds). Aurora NÃO fecha em Avios: TOS só ida, KEF ausente. Half-term 26-30/out; janela sáb24/out-dom1/nov (Theo volta seg2/nov).' \
  --status active \
  --intent-json '{"goal":"city/aurora family trip on Avios, Oct-2026 half-term, 3-4 nights, ONE weekend","pax":4,"origin_airports":["LHR","LGW","LCY","STN","LTN"],"destinations_considered":["CPH","MUC","aurora: TOS/KEF/Lapland"],"frontrunner":"CPH","window":{"depart":"2026-10-24","return":"2026-10-27 or 28","school_back":"2026-11-02"},"nights":"3-4","modes":["avios"],"finding":"cities have ample two-way award space; aurora-by-Avios does not close (TOS outbound-only, KEF absent)"}' \
  --payload-json '{"pax":4,"month":"2026-10","nights":"3-4","one_weekend_only":true,"window":{"depart_earliest":"2026-10-24","return_latest":"2026-11-01","school_back":"2026-11-02"},"school":{"region":"London state","oct_half_term_2026":"Mon 26 - Fri 30 Oct"},"radar_snapshot_2026-06-17":{"outbound":"AGP 5000 (LHR/LGW 9 seats) Oct27-30 & Nov1; FNC/LPA/TFS/ACE 6500 (9 seats) Oct29-Nov1; nothing Oct23-26","return":"ONLY FNC->LGW Oct28 9 seats 6500; no AGP/ACE/LPA/TFS returns in window"},"bottleneck":"return award space during half-term","options":[{"dest":"FNC","name":"Madeira","note":"best two-way footprint, NEW, kid-friendly ~23C Oct","avios_per_pax_per_leg":6500},{"dest":"AGP","name":"Malaga","avios_per_pax_per_leg":5000},{"dest":"ACE","name":"Lanzarote","avios_per_pax_per_leg":6500}],"strategy":["watch FNC returns Oct30-Nov1","or Avios outbound + cash return","compare vs Feb-2027 (dreambox id 1)"]}' | idof)
"$KELLY" session-attach-option "$sid" avios \
  --label 'Aurora (Tromsø) — outbound only, RT does NOT close in Avios' \
  --avios-points 6500 \
  --source seats_aero \
  --payload-json '{"route":"LHR->TOS","avios_per_pax_per_leg":6500,"outbound":{"date":"2026-10-27","flight":"BA612","dep":"07:50","arr":"12:30","seats":9},"return":"NONE in window (Oct26-Nov1) — bottleneck","kef":"Reykjavik absent from BA radar this window","verdict":"aurora-by-Avios not bookable RT now; would need cash return or partner connection"}'
"$KELLY" session-attach-option "$sid" avios \
  --label 'CPH round-trip — out Sat 24 Oct / back Wed 28 (4n), 4 pax' \
  --avios-points 32000 \
  --source seats_aero \
  --payload-json '{"route":"LHR<->CPH","pax":4,"avios_per_pax_per_leg":4000,"rt_total_avios_4pax":32000,"outbound":{"date":"2026-10-24","flight":"BA822","dep":"20:40","arr":"23:25","seats":9},"return_options":[{"date":"2026-10-27","flight":"BA819","dep":"06:25","seats":9,"nights":3},{"date":"2026-10-28","flight":"BA807","dep":"10:30","seats":9,"nights":4}],"taxes_note":"economy RFS off-peak; taxes/fees separate (low on CPH) — confirm on BA.com","bookable_now":true,"theo_school":"fully within Oct half-term, no missed school","kid_note":"Tivoli Halloween season late Oct"}'

echo ">> session: Half-term fev/2027 — sol em Avios (4 pax)"
sid=$("$KELLY" session-create 'Half-term fev/2027 — sol em Avios (4 pax)' \
  --destination 'Lanzarote (ACE) preferido; Gran Canária (LPA) / Málaga (AGP) alternativas' \
  --notes 'Karol+Luis+2 kids (≤11, inclui Theo na escola estadual de Londres). Janela SEM faltar aula: sair sáb 13/fev, voltar até dom 21/fev/2027 (volta às aulas seg 22). GARGALO: espaço-prêmio de VOLTA (ilhas→Londres) ainda não carregado no radar; ida só aparece meio de semana 16-21. Monitorar a volta.' \
  --status active \
  --intent-json '{"goal":"winter-sun family trip on Avios, Feb-2027 half-term, NEW destination (not Tenerife)","pax":4,"origin_airports":["LHR","LGW","LCY","STN","LTN"],"destinations":["ACE","LPA","AGP"],"window":{"depart":"2027-02-13 or 14","return":"2027-02-20 or 21","school_back":"2027-02-22"},"nights":"up to 8","modes":["avios"],"bottleneck":"return award space (islands->London) not yet loaded"}' \
  --payload-json '{"pax":4,"travellers":["Karol","Luis","kid","Theo"],"origin_airports":["LHR","LGW","LCY","STN","LTN"],"window":{"depart_earliest":"2027-02-13","return_latest":"2027-02-21","school_back":"2027-02-22"},"school":{"region":"London state","feb_half_term_2027":"Mon 15 - Fri 19 Feb","confirm":"check Theo'"'"'s specific school"},"already_done":["Tenerife","Chamonix"],"candidates":[{"dest":"ACE","name":"Lanzarote","avios_per_pax_per_leg":6500,"rt_4pax_avios":52000,"feb_temp_c":22,"note":"sol de verdade, family-friendly, NOVO"},{"dest":"LPA","name":"Gran Canaria","avios_per_pax_per_leg":6500,"rt_4pax_avios":52000,"feb_temp_c":22},{"dest":"AGP","name":"Malaga","avios_per_pax_per_leg":5000,"rt_4pax_avios":40000,"feb_temp_c":17,"note":"mais barato em Avios, ameno"}],"radar_snapshot_2026-06-17":{"outbound":"9 seats LON->ACE/LPA/TFS/AGP mostly Feb 16-21; nothing Feb 13-15","return":"NO award space islands->LON in Feb 18-21 yet (bottleneck)"},"avios_note":"BA RFS economy off-peak; taxes/fees separate, confirm on BA.com","next_step":"re-run kelly_avios_search return legs weekly; alert when ACE->LON (or LPA/AGP) opens on Feb 20-21"}' | idof)

echo "Seed complete. Verify with: $KELLY session-list && $KELLY profile-get albinati"
