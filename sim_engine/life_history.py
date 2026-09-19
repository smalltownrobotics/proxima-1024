"""Life histories — deterministic, empirically anchored fictional biographies.

Every person in the roster gets a stable psychological profile (Big Five +
general cognitive ability) and a year-by-year career arc (track, promotions,
discipline events, honors), generated lazily and reproducibly from the
campaign RNG seed. Nothing here draws from the world's tick RNG streams:
profiles are derived from sha256(seed, purpose, pid), so retrieval order,
new systems, and save/load cycles can never perturb the simulation — and a
person's history reads identically every time it is inspected, growing
append-only as they age (the Dwarf Fortress property).

PROVENANCE / HONEST REGISTER
    Empirically anchored distributions; fictional individuals and outcomes.
    This is not a psychological assessment instrument, not a clinical or
    actuarial model, and not a prediction about any real person. Published
    population statistics anchor the *shape* of the distributions and base
    rates; every individual value is invented game fiction.

EMPIRICAL ANCHORS (sources for the constants below)
    Big Five trait scores — T-score convention, mean 50, SD 10:
      - Costa, P. T., & McCrae, R. R. (1992). "NEO PI-R Professional Manual."
        Psychological Assessment Resources. (T-score normalization.)
      - Srivastava, S., John, O. P., Gosling, S. D., & Potter, J. (2003).
        "Development of personality in early and middle adulthood." JPSP 84(5).
        (Large-sample Big Five means/variances in the general population.)
      - Schmitt, D. P., et al. (2007). "The geographic distribution of Big Five
        personality traits." J. Cross-Cultural Psychology 38(2).
    General cognitive ability — deviation-IQ convention, mean 100, SD 15:
      - Wechsler, D. (2008). "WAIS-IV Technical Manual." Pearson.
    Heritability and regression to the mean:
      - Vukasović, T., & Bratko, D. (2015). "Heritability of personality: A
        meta-analysis of behavior genetic studies." Psych. Bulletin 141(4).
        (Personality h² ≈ 0.40 across traits and designs.)
      - Polderman, T. J. C., et al. (2015). "Meta-analysis of the heritability
        of human traits based on fifty years of twin studies." Nature Genetics
        47(7). (Cognitive ability h² ≈ 0.5, conservative adult midpoint.)
      - Falconer, D. S., & Mackay, T. F. C. (1996). "Introduction to
        Quantitative Genetics," 4th ed. (Midparent regression: expected child
        deviation = h² × midparent deviation; noise chosen to hold population
        variance stationary across generations.)
    Career base rates and trait conditioning:
      - ADP Research Institute, "Workforce Vitality Report" (2019): on the
        order of 8–9% of U.S. workers receive a promotion in a given year.
      - Schmidt, F. L., & Hunter, J. E. (1998). "The validity and utility of
        selection methods in personnel psychology." Psych. Bulletin 124(2).
        (General mental ability predicts job performance, ρ ≈ .51.)
      - Barrick, M. R., & Mount, M. K. (1991). "The Big Five personality
        dimensions and job performance: A meta-analysis." Personnel Psychology
        44(1). (Conscientiousness validity ρ ≈ .22–.31 across criteria.)
      - Judge, T. A., Bono, J. E., Ilies, R., & Gerhardt, M. W. (2002).
        "Personality and leadership." J. Applied Psychology 87(4).
        (Extraversion–leadership ρ ≈ .31; used for senior-grade promotion.)
      - DoD Joint Service Committee on Military Justice, annual reports:
        non-judicial punishment historically on the order of 1–2 actions per
        100 service members per year (used as the shipboard discipline anchor).
      - Gottfredson, M. R., & Hirschi, T. (1990). "A General Theory of Crime";
        Sweeten, Piquero, & Steinberg (2013), J. Youth & Adolescence 42.
        (Age–crime curve: misconduct peaks in late adolescence/early 20s and
        declines steeply with age — the discipline age factor below.)
      - Service commendation medals (e.g., Navy & Marine Corps Achievement
        Medal) are awarded to a low single-digit percentage of personnel per
        year — the order-of-magnitude anchor for the honors base rate.

The weights that combine trait z-scores into event rates are proportional to
the published validity coefficients above; the exponential link and clamps
are game-design choices, kept explicit in constants.

Public API
    LifeHistoryBook(seed, people)   — people is world.state['population']['people']
        .traits(pid)                — Big Five + GCA (memoized, heritable)
        .history(pid, current_year) — chronological career events
        .profile(pid, current_year) — full inspectable profile dict
    The bridge exposes this per-campaign via engine.person_history().

Cost: one profile uses a few thousand RNG draws. Traits memoize; histories
are recomputed per call from
the person's private stream, which keeps the Book stateless across saves.
"""
from __future__ import annotations

import hashlib
import math
import random
from typing import Any

# === Distribution constants (see docstring anchors) ===
BIG_FIVE = ("openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism")
TRAIT_MEAN, TRAIT_SD = 50.0, 10.0          # NEO T-score convention
TRAIT_CLAMP = (5.0, 95.0)
GCA_MEAN, GCA_SD = 100.0, 15.0             # deviation-IQ convention (WAIS)
GCA_CLAMP = (40.0, 160.0)
H2_PERSONALITY = 0.40                      # Vukasović & Bratko 2015
H2_GCA = 0.50                              # Polderman et al. 2015

PROVENANCE = ("Empirically anchored distributions; fictional individuals and outcomes. "
              "Not an assessment of, or prediction about, any real person.")

# === Career model constants ===
CAREER_START_AGE = 18
# Shipboard duty tracks; affinity weights are per-SD trait deviations applied
# to a softmax-style weighted choice. Light touch: tracks stay plausible for
# everyone, traits only tilt the odds.
TRACKS = {
    "operations":  {"label": "Ship operations",       "affinity": {"conscientiousness": 0.3, "extraversion": 0.2}},
    "engineering": {"label": "Engineering",           "affinity": {"openness": 0.2, "conscientiousness": 0.3}},
    "hydroponics": {"label": "Hydroponics & ecology", "affinity": {"conscientiousness": 0.2, "agreeableness": 0.2}},
    "medical":     {"label": "Medical service",       "affinity": {"agreeableness": 0.3, "conscientiousness": 0.2}},
    "sciences":    {"label": "Sciences & archive",    "affinity": {"openness": 0.5}},
    "education":   {"label": "Education & care",      "affinity": {"agreeableness": 0.4, "extraversion": 0.2}},
    "fabrication": {"label": "Fabrication & repair",  "affinity": {"conscientiousness": 0.2, "openness": 0.1}},
    "civic":       {"label": "Civic administration",  "affinity": {"extraversion": 0.3, "agreeableness": 0.2}},
}
GRADES = ("apprentice", "technician", "specialist", "senior specialist",
          "section chief", "department head")
PROMOTION_BASE = 0.09          # ADP: ~8–9% promoted per year
GRADE_STEP_FACTOR = 0.72       # each rung is scarcer than the one below
DISCIPLINE_BASE = 0.016        # ~1.6 formal actions / 100 person-years (NJP anchor)
HONOR_BASE = 0.022             # low single-digit % commendation incidence
# Performance composite weights ∝ published validities (Schmidt & Hunter 1998;
# Barrick & Mount 1991; Judge et al. 2002 folds in only at leadership grades).
PERF_W_GCA, PERF_W_CONSC, PERF_W_EXTRA_LEAD = 0.51, 0.31, 0.31
LEADERSHIP_GRADE = 3           # senior specialist and up: leadership loading applies


def _stream(seed: int, purpose: str, pid: int) -> random.Random:
    """Private deterministic stream per (campaign seed, purpose, person).

    Same recipe as framework.rng.DeterministicRng.fork — sha256-keyed
    independent streams — but never touching the world's tick generators.
    """
    h = hashlib.sha256(f"{seed}::{purpose}::{pid}".encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class LifeHistoryBook:
    """Deterministic biography source for one campaign's roster.

    Holds a live reference to the population slice's people map, so newborns
    are retrievable the tick they appear. Stateless beyond a traits memo —
    safe to rebuild from (seed, people) at any time, including after load.
    """

    def __init__(self, seed: int, people: dict):
        self.seed = int(seed)
        self.people = people
        self._traits: dict[int, dict] = {}

    # ---- roster access ----
    def _person(self, pid: Any) -> dict:
        p = self.people.get(pid)
        if p is None:
            try:
                p = self.people.get(int(pid))
            except (TypeError, ValueError):
                p = None
        if p is None:
            p = self.people.get(str(pid))
        if p is None:
            raise KeyError(f"unknown person: {pid!r}")
        return p

    # === Traits: Big Five + GCA, heritable across generations ===
    def traits(self, pid: Any) -> dict:
        person = self._person(pid)
        key = int(person["pid"])
        cached = self._traits.get(key)
        if cached is not None:
            return cached
        rng = _stream(self.seed, "traits", key)
        parents = person.get("parents")
        out: dict[str, float] = {}
        if not parents:
            # Founders: population-realistic draws. Founder screening is not
            # modeled (a real crew-selection shift is a future config knob);
            # the honest default is the general-population distribution.
            for trait in BIG_FIVE:
                out[trait] = _clamp(rng.gauss(TRAIT_MEAN, TRAIT_SD), *TRAIT_CLAMP)
            out["gca"] = _clamp(rng.gauss(GCA_MEAN, GCA_SD), *GCA_CLAMP)
        else:
            # Midparent regression (Falconer & Mackay): expected child deviation
            # is h² × midparent deviation; residual SD is chosen so population
            # variance stays stationary generation over generation:
            #   Var(child) = h⁴·σ²/2 + σ²(1 − h⁴/2) = σ²   (parents ≈ uncorrelated)
            ta = self.traits(parents[0])
            tb = self.traits(parents[1])
            for trait in BIG_FIVE:
                mid_dev = ((ta[trait] + tb[trait]) / 2.0) - TRAIT_MEAN
                noise = rng.gauss(0.0, TRAIT_SD * math.sqrt(1.0 - (H2_PERSONALITY ** 2) / 2.0))
                out[trait] = _clamp(TRAIT_MEAN + H2_PERSONALITY * mid_dev + noise, *TRAIT_CLAMP)
            mid_dev = ((ta["gca"] + tb["gca"]) / 2.0) - GCA_MEAN
            noise = rng.gauss(0.0, GCA_SD * math.sqrt(1.0 - (H2_GCA ** 2) / 2.0))
            out["gca"] = _clamp(GCA_MEAN + H2_GCA * mid_dev + noise, *GCA_CLAMP)
        self._traits[key] = out
        return out

    def _z(self, traits: dict, name: str) -> float:
        if name == "gca":
            return (traits["gca"] - GCA_MEAN) / GCA_SD
        return (traits[name] - TRAIT_MEAN) / TRAIT_SD

    # === Career arc: track, promotions, discipline, honors ===
    def history(self, pid: Any, current_year: int) -> list[dict]:
        """Chronological events from age 18 to the person's current (or death) age.

        Deterministic and append-only: asking again after the person ages a
        year replays the same past and adds at most a year of new events.
        """
        person = self._person(pid)
        key = int(person["pid"])
        traits = self.traits(key)
        rng = _stream(self.seed, "career", key)

        # Track: trait-tilted weighted choice (one draw, stable for life).
        weights = []
        for track in TRACKS.values():
            w = 1.0
            for trait, per_sd in track["affinity"].items():
                w *= math.exp(per_sd * self._z(traits, trait))
            weights.append(w)
        roll = rng.random() * sum(weights)
        track_id = next(iter(TRACKS))
        for tid, w in zip(TRACKS, weights):
            roll -= w
            if roll < 0:
                track_id = tid
                break

        age_now = int(person["age"])
        # Year the person turned each age. The alive branch anchors on
        # current_year; the dead branch anchors on the recorded death year, so
        # a casualty's biography is frozen at the moment they died.
        anchor_year = person["died_year"] if not person["alive"] and person.get("died_year") is not None else current_year
        birth_year = anchor_year - age_now  # mission year (negative = Earth-side, pre-launch)

        events: list[dict] = []
        grade = 0
        commendations = 0
        discipline_count = 0
        if age_now >= CAREER_START_AGE:
            events.append(self._event(CAREER_START_AGE, birth_year, "career_start", grade,
                                      f"Entered {TRACKS[track_id]['label']} as {GRADES[0]}"))
        z_gca = self._z(traits, "gca")
        z_c = self._z(traits, "conscientiousness")
        z_a = self._z(traits, "agreeableness")
        z_n = self._z(traits, "neuroticism")
        z_e = self._z(traits, "extraversion")
        for age in range(CAREER_START_AGE, age_now):
            # Fixed draw order per year (discipline, promotion, honor) — the
            # determinism contract depends on this order never changing.
            # -- discipline: trait- and age-conditioned (age–crime curve) --
            age_factor = 1.8 if age < 25 else 1.2 if age < 35 else 0.85 if age < 45 else 0.6
            d_rate = DISCIPLINE_BASE * age_factor * _clamp(
                math.exp(-0.45 * z_c - 0.30 * z_a + 0.25 * z_n), 0.15, 4.0)
            if rng.random() < d_rate:
                discipline_count += 1
                sev = rng.random()
                if sev < 0.70:
                    detail = "Formal reprimand recorded"
                elif sev < 0.95 or grade == 0:
                    detail = "Disciplinary citation and duty review"
                else:
                    grade -= 1
                    detail = f"Demoted to {GRADES[grade]} after disciplinary board"
                events.append(self._event(age, birth_year, "discipline", grade, detail))
            # -- promotion: performance composite ∝ published validities --
            perf = PERF_W_GCA * z_gca + PERF_W_CONSC * z_c
            if grade >= LEADERSHIP_GRADE:
                perf += PERF_W_EXTRA_LEAD * z_e
            p_rate = (PROMOTION_BASE * (GRADE_STEP_FACTOR ** grade)
                      * _clamp(math.exp(0.55 * perf), 0.25, 3.5))
            if grade < len(GRADES) - 1 and rng.random() < p_rate:
                grade += 1
                events.append(self._event(age, birth_year, "promotion", grade,
                                          f"Promoted to {GRADES[grade]}, {TRACKS[track_id]['label']}"))
            # -- honors --
            h_rate = HONOR_BASE * _clamp(math.exp(0.50 * perf), 0.25, 3.5)
            if rng.random() < h_rate:
                commendations += 1
                events.append(self._event(age, birth_year, "honor", grade,
                                          "Service commendation"))
        if not person["alive"] and person.get("died_year") is not None:
            cause = person.get("cause_of_death") or "unrecorded"
            events.append({"age": age_now, "year": person["died_year"],
                           "era": "aboard" if person["died_year"] >= 0 else "earthside",
                           "kind": "death", "grade": GRADES[grade],
                           "detail": f"Died in mission year {person['died_year']} ({cause})"})
        return events

    @staticmethod
    def _event(age: int, birth_year: int, kind: str, grade: int, detail: str) -> dict:
        year = birth_year + age
        return {"age": age, "year": year,
                "era": "earthside" if year < 0 else "aboard",
                "kind": kind, "grade": GRADES[grade], "detail": detail}

    # === Full inspectable profile ===
    def profile(self, pid: Any, current_year: int) -> dict:
        person = self._person(pid)
        traits = self.traits(pid)
        events = self.history(pid, current_year)
        grade = GRADES[0]
        track = None
        for e in events:
            if e["kind"] == "career_start":
                track = e["detail"].split("Entered ", 1)[-1].split(" as ")[0]
            if e["kind"] in ("career_start", "promotion", "discipline"):
                grade = e["grade"]
        return {
            "pid": int(person["pid"]),
            "generation": person["generation"],
            "age": int(person["age"]),
            "alive": bool(person["alive"]),
            "traits": {t: round(traits[t], 1) for t in BIG_FIVE},
            "trait_descriptors": {t: self._descriptor(self._z(traits, t)) for t in BIG_FIVE},
            "trait_scale": "T-score convention (population mean 50, SD 10)",
            "gca": round(traits["gca"], 1),
            "gca_scale": "deviation-IQ convention (population mean 100, SD 15)",
            "track": track,
            "grade": grade,
            "commendations": sum(1 for e in events if e["kind"] == "honor"),
            "discipline_events": sum(1 for e in events if e["kind"] == "discipline"),
            "events": events,
            "provenance": PROVENANCE,
        }

    @staticmethod
    def _descriptor(z: float) -> str:
        # Band edges at ±0.5/±1.5 SD — conventional qualitative T-score bands.
        if z <= -1.5:
            return "very low"
        if z <= -0.5:
            return "low"
        if z < 0.5:
            return "average"
        if z < 1.5:
            return "high"
        return "very high"
