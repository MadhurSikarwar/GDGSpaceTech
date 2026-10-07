"""The pure frontend modules (no DOM, no CesiumJS), executed with Node.

perf.js decides when the landing page should draw a cheaper picture; time.js turns UTC into India Standard Time.
Both are small, and a mistake in either is visible on every page, so they are tested for real rather than by eye.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parent.parent / "frontend" / "js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


def run(module, body):
    """Import frontend/js/<module> in Node, run `body` (which must `console.log(JSON.stringify(...))`)."""
    url = (JS / module).as_uri()
    code = f"import * as m from '{url}'; {body}"
    r = subprocess.run([NODE, "--input-type=module", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ----------------------------------------------------------------------------- perf.js
def test_fl01_a_healthy_page_is_left_alone():
    out = run("perf.js", """
      const steps = []; const g = m.createGovernor({ onStep: (l) => steps.push(l) });
      for (let i = 0; i < 2000; i++) g.frame(16.7);
      console.log(JSON.stringify({ steps, level: g.level }));""")
    assert out == {"steps": [], "level": 0}


def test_fl02_slow_frames_step_down_after_two_slow_windows_in_a_row():
    out = run("perf.js", """
      const at = []; let n = 0; const g = m.createGovernor({ frames: 45, windows: 2, onStep: (l) => at.push([l, n]) });
      for (n = 1; n <= 45 * 4; n++) g.frame(50);
      console.log(JSON.stringify({ at, level: g.level }));""")
    # the first step comes at the end of the second slow window (frame 90), the next at the end of the fourth (frame 180)
    assert out["at"] == [[1, 90], [2, 180]] and out["level"] == 2


def test_fl03_one_slow_window_between_good_ones_does_not_trigger_a_step():
    out = run("perf.js", """
      const g = m.createGovernor({ frames: 10, windows: 2, onStep: () => {} });
      for (let w = 0; w < 6; w++) for (let i = 0; i < 10; i++) g.frame(w % 2 ? 60 : 16);
      console.log(JSON.stringify({ level: g.level }));""")
    assert out["level"] == 0


def test_fl04_pauses_and_stalls_are_not_slow_rendering():
    out = run("perf.js", """
      const g = m.createGovernor({ frames: 10, windows: 1, onStep: () => {} });
      for (let i = 0; i < 500; i++) g.frame(i % 2 ? 3000 : 16);    // a hidden tab: huge gaps between frames
      for (let i = 0; i < 100; i++) { g.frame(0); g.frame(-5); g.frame(NaN); }
      console.log(JSON.stringify({ level: g.level }));""")
    assert out["level"] == 0


def test_fl05_it_never_steps_beyond_the_last_level_and_reset_clears_the_window():
    out = run("perf.js", """
      const g = m.createGovernor({ steps: 3, frames: 5, windows: 1, onStep: () => {} });
      for (let i = 0; i < 400; i++) g.frame(80);
      const top = g.level;
      const h = m.createGovernor({ steps: 3, frames: 5, windows: 1, onStep: () => {} });
      for (let i = 0; i < 4; i++) h.frame(80);   // one frame short of a window
      h.reset();
      h.frame(80);
      console.log(JSON.stringify({ top, afterReset: h.level }));""")
    assert out == {"top": 3, "afterReset": 0}


# ----------------------------------------------------------------------------- time.js
def test_fl06_india_standard_time_is_utc_plus_five_thirty():
    out = run("time.js", """
      console.log(JSON.stringify({
        a: m.istHMS('2026-10-06T14:09:19Z'),
        b: m.istDateTime('2026-10-06T20:00:00Z'),
        c: m.bothDateTime('2026-10-06T20:00:00Z'),
        d: m.bothDateTime('2026-10-06T10:00:00Z'),
        e: m.istHMS('2026-10-06 09:00:00'),                 // a MySQL-style stamp without a zone is UTC
        f: m.istDate(Date.UTC(2026, 11, 31, 18, 45)),
        g: m.istHMS(null),
        h: m.bothClock(new Date(Date.UTC(2026, 0, 1, 0, 0, 0))),
      }));""")
    assert out["a"] == "19:39:19"
    assert out["b"] == "2026-10-07 01:30:00 IST"                       # the date rolls over after 18:30 UTC
    assert out["c"] == "2026-10-07 01:30:00 IST · 10-06 20:00:00 UTC"  # and UTC shows its own date when it differs
    assert out["d"] == "2026-10-06 15:30:00 IST · 10:00:00 UTC"
    assert out["e"] == "14:30:00"
    assert out["f"] == "2027-01-01"
    assert out["g"] == "—"
    assert out["h"] == "05:30:00 IST · 00:00:00 UTC"


# ----------------------------------------------------------------------------- hud.js
def test_fl07_countdown_reads_t_minus_before_an_event_and_t_plus_after():
    out = run("hud.js", """
      console.log(JSON.stringify([m.countdown(3 * 3600e3 + 12 * 60e3 + 8e3), m.countdown(-250e3), m.countdown(0),
        m.countdown(2 * 86400e3 + 3661e3), m.countdown(999)]));""")
    assert out == ["T−03:12:08", "T+00:04:10", "T−00:00:00", "T−2d 01:01:01", "T−00:00:00"]


def test_fl08_hud_coordinates_wrap_and_carry_a_hemisphere():
    out = run("hud.js", """
      console.log(JSON.stringify([m.latLabel(14), m.latLabel(-82.04), m.latLabel(0), m.lonLabel(78.2), m.lonLabel(190),
        m.lonLabel(-47.3), m.lonLabel(-181)]));""")
    assert out == ["14.0°N", "82.0°S", "0.0°N", "78.2°E", "170.0°W", "47.3°W", "179.0°E"]


# ----------------------------------------------------------------------------- diagrams.js
def test_fl09_radar_places_events_by_time_clockwise_and_by_miss_from_the_centre():
    out = run("diagrams.js", """
      const p = (h, k) => { const q = m.radarPoint(h, k); return [Math.round(q.x), Math.round(q.y), Math.round(q.r)]; };
      console.log(JSON.stringify({ now: p(0, 10), quarter: p(18, 2.5), half: p(36, 0), full: p(72, 10), over: p(100, 99), neg: p(-5, -1) }));""")
    assert out == {"now": [210, 42, 168], "quarter": [294, 210, 84], "half": [210, 210, 0], "full": [210, 42, 168],
                   "over": [210, 42, 168], "neg": [210, 210, 0]}      # the top is now, a quarter turn is a quarter of the span; sqrt of the miss


def test_fl10_next_events_are_the_future_ones_soonest_first():
    out = run("diagrams.js", """
      const now = Date.parse('2026-10-07T00:00:00Z');
      const ev = (id, tca) => ({ event_id: id, tca, miss_km: 1, risk: 'HIGH', primary: { name: 'A' }, secondary: { name: 'B' } });
      const list = [ev(1, '2026-10-07T05:00:00Z'), ev(2, '2026-10-06T23:00:00Z'), ev(3, '2026-10-07T01:00:00Z'), ev(4, '2026-10-07T02:00:00Z')];
      console.log(JSON.stringify({ ids: m.nextEvents(list, now).map((e) => e.event_id), two: m.nextEvents(list, now, 2).map((e) => e.event_id),
        dots: (m.radarSvg(list, { now }).match(/class="rd-dot"/g) || []).length }));""")
    assert out == {"ids": [3, 4, 1], "two": [3, 4], "dots": 3}


def test_fl11_the_orbit_diagram_is_to_scale_with_the_earth_at_the_focus():
    out = run("diagrams.js", """
      const r = (g) => ({ earth: g.earthR, A: g.A, B: g.B, shift: g.ex - g.cx });
      console.log(JSON.stringify({ iss: r(m.orbitGeometry({ perigee_km: 416, apogee_km: 425 })),
        geo: r(m.orbitGeometry({ perigee_km: 35780, apogee_km: 35792 })), heo: r(m.orbitGeometry({ perigee_km: 600, apogee_km: 39700 })) }));""")
    assert out["iss"]["earth"] / out["iss"]["A"] == pytest.approx(6378.137 / (6378.137 + 420.5), rel=1e-6)
    assert out["geo"]["earth"] / out["geo"]["A"] == pytest.approx(6378.137 / (6378.137 + 35786), rel=1e-4)
    assert out["heo"]["shift"] > 50 and out["heo"]["A"] > out["heo"]["B"]       # the focus sits towards perigee in an eccentric orbit


def test_fl12_altitude_bar_is_a_log_scale_with_the_regime_ticks_in_order():
    out = run("diagrams.js", """
      const pos = (km) => +m.altPos(km).toFixed(3);
      const bar = m.altitudeBar(416, 425);
      const ticks = [...bar.matchAll(/<b style="left:([0-9.]+)%"/g)].map((x) => Number(x[1]));
      console.log(JSON.stringify({ floor: pos(10), bottom: pos(100), iss: pos(420), leo: pos(2000), geo: pos(35786), top: pos(600000), over: pos(9e9), nan: pos(null),
        ticks, bar: bar.includes('class="altbar"'), width: bar.match(/width:([0-9.]+)%/)[1] }));""")
    assert out["floor"] == out["bottom"] == 0 and out["top"] == out["over"] == 1 and out["nan"] == 0
    assert out["iss"] < out["leo"] < out["geo"] < 1 and out["ticks"] == [round(out["leo"] * 100, 1), round(out["geo"] * 100, 1)]
    assert out["bar"] and float(out["width"]) < 1.0                  # nine kilometres of altitude difference is a sliver


def test_fl13_encounter_preview_numbers_come_from_the_form_and_the_orbit():
    out = run("diagrams.js", """
      const svg = m.encounterSvg({ missKm: 0.25, crossingDeg: 70, leadH: 6, periodMin: 92.9, altKm: 420 });
      const texts = [...svg.matchAll(new RegExp('>([^<>]+)</text>', 'g'))].map((x) => x[1]);
      console.log(JSON.stringify({ v: +m.circularSpeed(420).toFixed(2), closing: +m.closingSpeed(7.66, 70).toFixed(2), head: +m.closingSpeed(7.66, 180).toFixed(2),
        ticks: (svg.match(/<line x1=/g) || []).length, texts,
        fallback: m.encounterSvg({ missKm: NaN, crossingDeg: NaN, leadH: NaN }).includes('70°') }));""")
    assert out["v"] == 7.66 and out["closing"] == 8.79 and out["head"] == 15.32       # 2 v sin(angle / 2): two equal speeds meeting
    assert out["ticks"] == 7                                                           # 6 h of 46.45-minute half-orbits
    assert "MISS 0.25 KM" in out["texts"] and "70°" in out["texts"] and any("7 HALF-ORBITS" in t for t in out["texts"])
    assert out["fallback"]                                                             # an empty box means the default angle


# ----------------------------------------------------------------------------- shortcuts.js
def test_fl14_the_second_key_of_a_shortcut_has_to_be_known_and_come_soon():
    out = run("shortcuts.js", """
      const t = 1000000;
      console.log(JSON.stringify({ d: m.destination('d', t, t + 200), upper: m.destination('D', t, t + 200), late: m.destination('d', t, t + 1500),
        none: m.destination('d', 0, t), unknown: m.destination('z', t, t + 100), keys: Object.keys(m.GO) }));""")
    assert out["d"][0] == "#/dashboard" and out["upper"][0] == "#/dashboard"
    assert out["late"] is None and out["none"] is None and out["unknown"] is None      # too slow, no first key, not a shortcut
    assert out["keys"] == ["h", "g", "d", "c", "a", "l", "i"]


# ----------------------------------------------------------------------------- plain.js
def test_fl15_probabilities_and_spans_are_said_in_words():
    out = run("plain.js", """
      console.log(JSON.stringify({ a: m.oneIn(1.7e-6), b: m.oneIn(3e-9), c: m.oneIn(0.3), d: m.oneIn(2e-4), e: m.oneIn(1e-13), z: m.oneIn(0),
        n: m.oneIn(null), big: m.oneIn(0.9), s: [m.span(1), m.span(51), m.span(84.6), m.span(300), m.span(4000), m.span(-30)] }));""")
    assert out["a"] == "1 in 590,000" and out["b"] == "1 in 330 million" and out["c"] == "1 in 3.3" and out["d"] == "1 in 5,000"
    assert out["e"] == "less than 1 in a trillion" and out["z"] == "practically zero" and out["n"] is None and out["big"] == "more likely than not"
    assert out["s"] == ["1 minute", "51 minutes", "85 minutes", "5 hours", "2.8 days", "30 minutes"]


NO_FEASIBLE = """{ decision: 'NO_FEASIBLE_MANEUVER', status: 'complete', risk_tier: 'HIGH', pc_before: 1.7e-6,
  subject: { primary_name: 'IRS-P3', secondary_name: 'METEOR 1-5 DEB', time_of_closest_approach: '2026-10-07T11:36:49Z', miss_distance_km: 2.451 },
  steps: [{ tool_name: 'generate_maneuver_candidates', payload: { candidates: [{ candidate_id: 'M1', lead_time_min: 51, direction: 'RADIAL IN', dv_mps: 0.012 }] } },
          { tool_name: 'evaluate_maneuver_constraints', payload: { candidate_id: 'M1', feasible: false, violations: ['uplink_window_before_burn'] } }] }"""


def test_fl16_a_case_with_no_feasible_burn_says_why_in_plain_words():
    out = run("plain.js", f"""
      const e = m.explain({NO_FEASIBLE}, Date.parse('2026-10-07T10:16:00Z'));
      console.log(JSON.stringify(e));""")
    assert out["tone"] == "warn" and out["title"] == "No workable way to avoid it"
    assert out["summary"][0] == "IRS-P3 and METEOR 1-5 DEB will pass about 2.45 km from each other at 2026-10-07 17:06 IST (in about 81 minutes)."
    assert "about 1 in 590,000" in out["summary"][1] and "rates the event HIGH risk" in out["summary"][1]
    assert out["summary"][2] == "The planner tried one way to steer clear, but it cannot be carried out."
    assert [f["key"] for f in out["facts"]] == ["pc", "miss", "tca"] and out["facts"][0]["detail"] == "1.7e-6"
    assert len(out["options"]) == 1 and out["options"][0]["ok"] is False
    assert out["options"][0]["line"] == ("M1: a burn 51 minutes before closest approach, pushing towards the Earth: cannot be carried out, "
                                         "because no ground station can send the command to the satellite before the burn")
    assert "operator" in out["next"]


def test_fl17_other_answers_and_missing_data_never_break_the_explanation():
    out = run("plain.js", """
      const now = Date.parse('2026-10-07T10:16:00Z');
      const rec = m.explain({ decision: 'MANEUVER_RECOMMENDED', status: 'complete', risk_tier: 'CRITICAL', pc_before: 1.9e-4,
        maneuver: { dv_mps: 0.514, direction: 'POSIGRADE', lead_time_min: 47, miss_after_km: 5.02, pc_after: 2e-12 },
        subject: { primary_name: 'ISS (ZARYA)', secondary_name: 'DEBRIS', time_of_closest_approach: '2026-10-07T02:22:00Z', miss_distance_km: 0.2 } }, now);
      const mon = m.explain({ decision: 'MONITOR', status: 'complete', risk_tier: 'LOW', pc_before: 1e-9, subject: {} }, now);
      const gone = m.explain({ decision: 'DATA_UNAVAILABLE', status: 'complete', subject: {} }, now);
      const failed = m.explain({ status: 'failed' }, now);
      const bare = m.explain({}, now);
      console.log(JSON.stringify({ rec, mon: [mon.tone, mon.summary, mon.facts.length], gone: [gone.tone, gone.title], failed: [failed.tone, failed.title], bare: [bare.tone, bare.title, bare.summary, bare.options] }));""")
    assert out["rec"]["tone"] == "act" and "passed about 0.2 km" in out["rec"]["summary"][0] and "7.9 hours ago" in out["rec"]["summary"][0]
    assert "0.514 m/s, pushing forward along its path (speeds it up), about 47 minutes before closest approach" in out["rec"]["summary"][2]
    assert "about 1 in 500 billion" in out["rec"]["summary"][2] and "about 5 km" in out["rec"]["summary"][2]
    assert out["mon"] == ["ok", ["The chance that they actually collide is about 1 in 1 billion; OrbitWatch rates the event LOW risk.", "The risk is low enough that no burn is needed."], 1]
    assert out["gone"] == ["na", "Not enough information to advise"] and out["failed"] == ["na", "The assessment did not finish"]
    assert out["bare"] == ["na", "No recommendation yet", [], []]


def test_fl18_every_check_the_planner_can_fail_has_a_plain_reason():
    import re
    src = (Path(__file__).resolve().parent.parent / "orbitwatch" / "agent" / "tools.py").read_text(encoding="utf-8")
    block = src[src.index("checks = {"):src.index("violations = [")]
    names = set(re.findall(r'"(\w+)":', block)) | set(re.findall(r'checks\["(\w+)"\]', src))
    assert len(names) >= 7                                                    # the six fixed checks and the reviewer's minimum miss
    assert names <= set(run("plain.js", "console.log(JSON.stringify(Object.keys(m.WHY_NOT)));"))
