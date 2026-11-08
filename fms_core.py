from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
import re
import json
import math
import copy
import urllib.parse
import urllib.request
import urllib.error

from vnav_engine import VnavEngine, VnavPointPrediction, VnavProfileSummary
from performance_engine import (
    FuelPlan,
    LandingPerformance,
    TakeoffPerformance,
    WeightAndBalance,
)


@dataclass
class WaypointLeg:
    ident: str
    via: str = "DCT"
    segment_type: str = "ENRT"
    lat: Optional[float] = None
    lon: Optional[float] = None
    speed_constraint: str = ""
    alt_constraint: str = ""
    hold: Optional[Dict[str, str]] = None
    is_discontinuity: bool = False
    is_synthetic: bool = False

    @classmethod
    def create_discontinuity(cls) -> "WaypointLeg":
        return cls(
            ident="--- DISCONTINUITY ---",
            via="--",
            segment_type="DISCONTINUITY",
            is_discontinuity=True,
            is_synthetic=False,
        )

    @classmethod
    def create_synthetic(cls, ident: str, segment_type: str, alt_constraint: str = "") -> "WaypointLeg":
        return cls(
            ident=ident,
            via="--",
            segment_type=segment_type,
            alt_constraint=alt_constraint,
            is_synthetic=True,
            is_discontinuity=False,
        )


@dataclass
class FlightPlanBuffer:
    origin: str = "EGKK"
    destination: str = "EGCC"
    alternate: str = ""
    flight_number: str = ""
    crz_alt: str = "FL230"
    etd_utc: str = ""
    route_base: List[str] = field(default_factory=lambda: ["EGKK", "EGCC"])
    route_via: Dict[str, str] = field(default_factory=dict)
    procedures: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    constraints: Dict[str, Dict[str, str]] = field(default_factory=dict)
    holds: Dict[str, Dict[str, str]] = field(default_factory=dict)
    expanded_route: List[str] = field(default_factory=lambda: ["EGKK", "EGCC"])

    wb: WeightAndBalance = field(default_factory=WeightAndBalance)
    takeoff_perf: TakeoffPerformance = field(default_factory=TakeoffPerformance)
    landing_perf: LandingPerformance = field(default_factory=LandingPerformance)

    def clone(self) -> "FlightPlanBuffer":
        return copy.deepcopy(self)


class NavDatabase:
    """Pure Python X-Plane 12 Navdata & CIFP Manager."""

    def __init__(self):
        self.custom_data_dir: Optional[Path] = None
        self.proc_dir: Optional[Path] = None
        self.status: Dict[str, str] = {
            "overall": "NOT LOADED",
            "cycle": "--",
            "format": "--",
            "cifp": "NOT FOUND",
            "proc": "NOT FOUND",
        }
        self.cifp_index: Dict[str, Path] = {}
        self.nav_ident_index: Dict[str, Set[str]] = {}
        self.nav_coords: Dict[str, Tuple[float, float]] = {}
        self.nav_coord_candidates: Dict[str, List[Tuple[float, float, str, str]]] = {}
        self.nav_airport_coords: Dict[str, Tuple[float, float]] = {}
        self._airway_cache: Optional[Dict[str, Dict[str, Set[str]]]] = None

    def scan_directory(self, selected_path: Path | str) -> Dict[str, str]:
        root = Path(selected_path).expanduser()
        if not root.is_dir():
            raise FileNotFoundError(f"Directory not found: {root}")

        candidates = [root]
        for candidate in (root / "Custom Data", root / "Resources" / "default data"):
            if candidate.is_dir():
                candidates.append(candidate)

        chosen = next(
            (
                d
                for d in candidates
                if any(
                    (d / f).is_file()
                    for f in ("earth_fix.dat", "earth_nav.dat", "earth_awy.dat", "earth_424.dat")
                )
            ),
            root,
        )
        self.custom_data_dir = chosen
        self._airway_cache = None

        files = {
            name: (chosen / name).is_file()
            for name in (
                "earth_fix.dat",
                "earth_nav.dat",
                "earth_awy.dat",
                "earth_hold.dat",
                "earth_424.dat",
                "user_fix.dat",
                "user_nav.dat",
            )
        }
        cifp = chosen / "CIFP"
        cifp_files = list(cifp.glob("*.dat")) if cifp.is_dir() else []

        proc_candidates = [
            chosen / "navdata" / "Proc",
            chosen / "Proc",
            chosen.parent / "navdata" / "Proc",
            chosen.parent / "Custom Data" / "navdata" / "Proc",
        ]
        self.proc_dir = next((d for d in proc_candidates if d.is_dir()), None)

        cycle = "--"
        for name in ("earth_fix.dat", "earth_nav.dat", "earth_awy.dat", "earth_424.dat"):
            f = chosen / name
            if f.is_file():
                try:
                    with f.open("r", encoding="utf-8", errors="ignore") as stream:
                        for _ in range(30):
                            line = stream.readline()
                            if not line:
                                break
                            m = re.search(
                                r"(?:AIRAC|CYCLE|CYCLE\s+ID)\s*[:= ]\s*([A-Z0-9-]{3,12})",
                                line,
                                re.I,
                            )
                            if m:
                                cycle = m.group(1)
                                break
                except OSError:
                    pass
            if cycle != "--":
                break

        text_files = any(files[n] for n in ("earth_fix.dat", "earth_nav.dat", "earth_awy.dat"))
        format_label = (
            "ARINC 424 master (earth_424.dat)"
            if files["earth_424.dat"] and not text_files
            else ("X-Plane text navdata" if text_files else "Unknown / incomplete")
        )
        overall = (
            "LOADED (PARTIAL CHECK)"
            if (text_files or files["earth_424.dat"])
            else "NAV FILES NOT FOUND"
        )
        self.status = {
            "overall": overall,
            "cycle": cycle,
            "format": format_label,
            "cifp": f"{len(cifp_files)} airport files" if cifp_files else "NOT FOUND",
            "proc": str(self.proc_dir) if self.proc_dir else "NOT FOUND",
        }

        self.cifp_index = {f.stem.upper(): f for f in cifp_files}
        self.nav_ident_index.clear()
        self.nav_coords.clear()
        self.nav_coord_candidates.clear()
        self.nav_airport_coords.clear()

        aptmeta = chosen / "earth_aptmeta.dat"
        if aptmeta.is_file():
            try:
                with aptmeta.open("r", encoding="utf-8", errors="ignore") as stream:
                    for line in stream:
                        parts = line.split()
                        if len(parts) < 5 or parts[0] in ("I", "A", "1210"):
                            continue
                        ident = parts[0].upper()
                        if not re.fullmatch(r"[A-Z0-9]{3,4}", ident):
                            continue
                        try:
                            lat, lon = float(parts[2]), float(parts[3])
                        except (ValueError, IndexError):
                            continue
                        if -90 <= lat <= 90 and -180 <= lon <= 180:
                            self.nav_airport_coords[ident] = (lat, lon)
            except OSError:
                pass

        for filename in ("earth_fix.dat", "earth_nav.dat"):
            path = chosen / filename
            if not path.is_file():
                continue
            try:
                with path.open("r", encoding="utf-8", errors="ignore") as stream:
                    for line in stream:
                        parts = line.split()
                        if not parts or parts[0] in ("I", "A"):
                            continue
                        if filename == "earth_fix.dat":
                            if len(parts) < 5:
                                continue
                            try:
                                lat, lon = float(parts[0]), float(parts[1])
                            except (ValueError, IndexError):
                                continue
                            ident, airport = parts[2].upper(), parts[3].upper()
                            if re.fullmatch(r"[A-Z0-9]{2,7}", ident):
                                candidate = (lat, lon, airport, parts[4].upper())
                                self.nav_coord_candidates.setdefault(ident, []).append(candidate)
                                self.nav_coords.setdefault(ident, (lat, lon))
                        else:
                            if not parts[0].lstrip("+-").isdigit() or len(parts) < 9:
                                continue
                            try:
                                lat, lon = float(parts[1]), float(parts[2])
                            except (ValueError, IndexError):
                                continue
                            ident = parts[7].upper()
                            if re.fullmatch(r"[A-Z0-9]{2,7}", ident):
                                candidate = (lat, lon, "NAV", parts[0])
                                self.nav_coord_candidates.setdefault(ident, []).append(candidate)
                                self.nav_coords.setdefault(ident, (lat, lon))
                        for token in parts:
                            token = token.upper().strip()
                            if re.fullmatch(r"[A-Z0-9]{2,7}", token):
                                self.nav_ident_index.setdefault(token, set()).add(filename)
            except OSError:
                continue

        return self.status

    def search_ident(self, ident: str, airway: str = "") -> List[str]:
        q = (ident or "").strip().upper()
        a = (airway or "").strip().upper()
        lines = []
        if not self.custom_data_dir:
            lines.append("NO CUSTOM DATA FOLDER SELECTED")
        elif q:
            cifp_path = self.cifp_index.get(q)
            sources = sorted(self.nav_ident_index.get(q, set()))
            if cifp_path:
                lines.append(f"{q}: AIRPORT CIFP FILE FOUND: {cifp_path.name}")
            if sources:
                lines.append(f"{q}: IDENTIFIER TOKEN FOUND IN {', '.join(sources)}")
            if not cifp_path and not sources:
                lines.append(f"{q}: NO MATCH IN INDEXED TEXT NAV FILES / CIFP FILENAMES")
        else:
            lines.append("Enter an airport, fix or navaid identifier.")
        if a:
            graph = self.airway_graph()
            if a in graph:
                lines.append(f"AIRWAY {a}: {len(graph[a])} connected fixes indexed in earth_awy.dat.")
            else:
                lines.append(f"AIRWAY {a}: NOT FOUND in earth_awy.dat.")
        return lines

    def airway_graph(self) -> Dict[str, Dict[str, Set[str]]]:
        if self._airway_cache is not None:
            return self._airway_cache

        candidates = []
        if self.custom_data_dir:
            custom = Path(self.custom_data_dir).expanduser()
            candidates.extend(
                [
                    custom / "earth_awy.dat",
                    custom / "Custom Data" / "earth_awy.dat",
                    custom / "Resources" / "default data" / "earth_awy.dat",
                ]
            )
        candidates.append(Path(__file__).resolve().parent / "earth_awy.dat")
        path = next((p for p in candidates if p.is_file()), None)
        graph: Dict[str, Dict[str, Set[str]]] = {}
        if path is None:
            self._airway_cache = graph
            return graph

        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for raw in stream:
                parts = raw.split()
                if len(parts) < 11 or parts[0] == "I":
                    continue
                a, b, airway = parts[0].upper(), parts[3].upper(), parts[-1].upper()
                if re.fullmatch(r"[A-Z]{1,3}[0-9]{1,4}[A-Z]?", airway):
                    graph.setdefault(airway, {}).setdefault(a, set()).add(b)
                    graph.setdefault(airway, {}).setdefault(b, set()).add(a)
        self._airway_cache = graph
        return graph

    def find_airway_path(self, airway: str, start: str, end: str) -> Optional[List[str]]:
        graph = self.airway_graph()
        adj = graph.get(airway.upper(), {})
        start, end = start.upper(), end.upper()
        if start not in adj or end not in adj:
            return None
        queue, seen = [(start, [start])], {start}
        while queue:
            node, path = queue.pop(0)
            if node == end:
                return path
            for nxt in adj.get(node, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, path + [nxt]))
        return None

    def parse_cifp_airport(self, airport: str) -> Dict[str, Any]:
        path = self.cifp_index.get((airport or "").strip().upper())
        data: Dict[str, Any] = {
            "runways": set(),
            "sids": {},
            "stars": {},
            "approaches": {},
            "raw_counts": {},
            "cifp_path": path,
        }
        if not path:
            return data

        try:
            with Path(path).open("r", encoding="utf-8", errors="replace") as stream:
                for raw in stream:
                    line = raw.strip()
                    if not line or ":" not in line:
                        continue
                    record, payload = line.split(":", 1)
                    record = record.strip().upper()
                    fields = [part.strip() for part in payload.rstrip("; ").split(",")]
                    data["raw_counts"][record] = data["raw_counts"].get(record, 0) + 1
                    if record == "RWY":
                        runway = fields[0].upper() if fields else ""
                        if re.fullmatch(r"RW\d{2}[LCR]?", runway):
                            data["runways"].add(runway[2:])
                    elif record in ("SID", "STAR") and len(fields) >= 6:
                        proc, runway = fields[2].upper(), fields[3].upper()
                        transition = fields[4].upper()
                        if proc:
                            bucket = data["sids"] if record == "SID" else data["stars"]
                            item = bucket.setdefault(proc, {"runways": set(), "transitions": set()})
                            if re.fullmatch(r"RW\d{2}[LCR]?", runway):
                                item["runways"].add(runway[2:])
                            if transition and transition not in ("ALL", "NONE", "-", "----"):
                                item["transitions"].add(transition)
                    elif record == "APPCH" and len(fields) >= 4:
                        approach = fields[2].upper()
                        transition = fields[3].upper()
                        if approach:
                            item = data["approaches"].setdefault(approach, {"transitions": set()})
                            if transition and transition not in (approach, "ALL", "NONE", "-", "----"):
                                item["transitions"].add(transition)
        except OSError as exc:
            data["error"] = str(exc)
        return data

    def get_procedure_fixes(
        self,
        airport: str,
        record_type: str,
        procedure: str,
        runway: Optional[str] = None,
        transition: Optional[str] = None,
    ) -> List[Tuple[str, str, str]]:
        """Extract ordered procedure legs from X-Plane CIFP records."""
        airport_clean = (airport or "").strip().upper()
        path = self.cifp_index.get(airport_clean)
        if not path or not procedure:
            return []
        record_type = record_type.upper()
        procedure = procedure.upper().strip()
        runway = (runway or "").upper().replace("RW", "").strip()
        transition = (transition or "").upper().strip()
        records = []

        try:
            with Path(path).open("r", encoding="utf-8", errors="replace") as stream:
                for raw in stream:
                    line = raw.strip()
                    if not line.startswith(record_type + ":"):
                        continue
                    payload = line.split(":", 1)[1]
                    fields = [part.strip().upper() for part in payload.rstrip("; ").split(",")]
                    if len(fields) < 5 or fields[2] != procedure:
                        continue

                    leg_type = ""
                    for f in fields[4:8]:
                        if f in ("IF", "TF", "CF", "DF", "FA", "CA", "VA", "FM", "VM", "VI", "CI", "CD", "VD"):
                            leg_type = f
                            break

                    if record_type in ("SID", "STAR"):
                        rec_runway = fields[3].replace("RW", "").strip()
                        if runway and rec_runway not in (runway, "", "ALL"):
                            continue
                        fix = fields[4].strip()
                    elif record_type == "APPCH":
                        if len(fields) < 5:
                            continue
                        rec_transition = fields[3].strip()
                        if transition and rec_transition not in (
                            transition,
                            "",
                            "ALL",
                            "NONE",
                            "-",
                            "----",
                        ):
                            continue
                        fix = fields[4].strip()
                    else:
                        continue

                    try:
                        seq = int(fields[0])
                    except (ValueError, IndexError):
                        seq = 999999

                    alt_constraint = ""
                    spd_constraint = ""
                    for token in fields[5:]:
                        if re.fullmatch(r"[+-]?(?:\d{3,5}|FL\d{2,3})", token):
                            if not alt_constraint:
                                alt_constraint = token
                        elif re.fullmatch(r"(?:1[5-9]\d|2[0-9]\d)", token):
                            if not spd_constraint:
                                spd_constraint = token

                    if len(fields) >= 11 and fix:
                        try:
                            lat = float(fields[9])
                            lon = float(fields[10])
                            if -90 <= lat <= 90 and -180 <= lon <= 180:
                                self.nav_coords[f"{airport_clean}_{fix}"] = (lat, lon)
                                self.nav_coords.setdefault(fix, (lat, lon))
                        except (ValueError, IndexError):
                            pass

                    resolved_fix = fix
                    if not resolved_fix or resolved_fix in ("-", "----"):
                        if leg_type in ("VM", "FM"):
                            resolved_fix = "(VECT)"
                        elif leg_type in ("CA", "VA", "FA"):
                            alt_val = alt_constraint.lstrip("+-") or "3000"
                            resolved_fix = f"({alt_val})"
                        elif leg_type in ("CI", "VI"):
                            resolved_fix = "(INTC)"
                        elif leg_type in ("CD", "VD"):
                            resolved_fix = "(DME)"

                    if resolved_fix and (
                        resolved_fix.startswith("(")
                        or re.fullmatch(r"[A-Z0-9]{2,7}", resolved_fix)
                    ):
                        if len(resolved_fix) == 2 and resolved_fix in ("EG", "UK", "US", "CA", "IN"):
                            continue
                        records.append((seq, resolved_fix, alt_constraint, spd_constraint))

        except (OSError, TypeError):
            return []

        records.sort(key=lambda item: item[0])
        result: List[Tuple[str, str, str]] = []
        for _, fix, alt_c, spd_c in records:
            if not result or result[-1][0] != fix:
                result.append((fix, alt_c, spd_c))
                if record_type == "APPCH" and re.fullmatch(r"RW\d{2}[LCR]?", fix):
                    break

        if record_type in ("SID", "STAR") and transition:
            transition_positions = [i for i, (fix, _, _) in enumerate(result) if fix == transition]
            if transition_positions and record_type == "SID":
                result = result[: transition_positions[-1] + 1]
        return result

    def resolve_route_coordinates(self, route: List[str]) -> Dict[str, Tuple[float, float]]:
        resolved: Dict[str, Tuple[float, float]] = {}
        if not route:
            return resolved

        origin = str(route[0]).strip().upper()
        destination = str(route[-1]).strip().upper()
        dest_coords = self.nav_airport_coords.get(destination)

        for index, fix in enumerate(route):
            ident = str(fix).strip().upper()

            # 1. Airport reference coordinates
            if index in (0, len(route) - 1) and ident in self.nav_airport_coords:
                resolved[ident] = self.nav_airport_coords[ident]
                continue

            # 2. Runway thresholds (e.g., RW23L) must anchor to destination airport
            if re.fullmatch(r"RW\d{2}[LCR]?", ident):
                scoped_key = f"{destination}_{ident}"
                if scoped_key in self.nav_coords:
                    resolved[ident] = self.nav_coords[scoped_key]
                elif dest_coords:
                    resolved[ident] = dest_coords
                continue

            # 3. Standard fix candidate resolution
            raw_candidates = list(self.nav_coord_candidates.get(ident, []))
            candidates = []
            seen_positions = set()
            for candidate in raw_candidates:
                position = (
                    round(float(candidate[0]), 8),
                    round(float(candidate[1]), 8),
                    str(candidate[2]).upper(),
                )
                if position not in seen_positions:
                    seen_positions.add(position)
                    candidates.append(candidate)

            if not candidates:
                scoped_dest = f"{destination}_{ident}"
                scoped_orig = f"{origin}_{ident}"
                if scoped_dest in self.nav_coords:
                    resolved[ident] = self.nav_coords[scoped_dest]
                elif scoped_orig in self.nav_coords:
                    resolved[ident] = self.nav_coords[scoped_orig]
                else:
                    fallback = self.nav_coords.get(ident)
                    if fallback is not None:
                        resolved[ident] = fallback
                continue

            chosen = None
            for airport in (destination, origin):
                chosen = next((c for c in candidates if str(c[2]).upper() == airport), None)
                if chosen is not None:
                    break

            if chosen is None:
                enroute = [c for c in candidates if str(c[2]).upper() == "ENRT"]
                if len(enroute) == 1:
                    chosen = enroute[0]
                elif len(candidates) == 1:
                    chosen = candidates[0]

            if chosen is not None:
                resolved[ident] = (chosen[0], chosen[1])
        return resolved


class FMSCore:
    """Pure Python A220 FMS State Machine (ACT / MOD / SEC)."""

    def __init__(self, navdb: Optional[NavDatabase] = None):
        self.navdb = navdb or NavDatabase()
        self.plan_mode: str = "ACT"
        self.mod_pending: bool = False

        self.act_plan = FlightPlanBuffer()
        self.sec_plan = self.act_plan.clone()
        self.mod_plan: Optional[FlightPlanBuffer] = None

        self.manual_groundspeed: int = 450
        self.departure_utc: str = ""
        self.departure_fob_kg: Optional[float] = None
        self.actual_fuel_flow_kgph: Optional[float] = None
        self.planned_departure_fob_kg: Optional[float] = None
        self.planned_fuel_flow_kgph: Optional[float] = None

        self.trans_alt_ft: int = 18000
        self.vpa_deg: float = -3.0

    def active_buffer(self) -> FlightPlanBuffer:
        if self.plan_mode == "SEC":
            return self.sec_plan
        if self.mod_pending and self.mod_plan is not None:
            return self.mod_plan
        return self.act_plan

    def ensure_mod_buffer(self) -> FlightPlanBuffer:
        if self.plan_mode == "SEC":
            return self.sec_plan
        if not self.mod_pending or self.mod_plan is None:
            self.mod_plan = self.act_plan.clone()
            self.mod_pending = True
        return self.mod_plan

    def toggle_plan_mode(self) -> str:
        self.plan_mode = "SEC" if self.plan_mode == "ACT" else "ACT"
        return self.plan_mode

    def copy_act_to_sec(self):
        self.sec_plan = self.act_plan.clone()

    def stage_activate_sec(self):
        self.mod_plan = self.sec_plan.clone()
        self.mod_pending = True

    def execute_mod(self) -> bool:
        if self.plan_mode == "SEC":
            self.act_plan, self.sec_plan = self.sec_plan.clone(), self.act_plan.clone()
            self.plan_mode = "ACT"
            return True
        if self.mod_pending and self.mod_plan is not None:
            self.act_plan = self.mod_plan.clone()
            self.mod_plan = None
            self.mod_pending = False
            return True
        return False

    def cancel_mod(self) -> bool:
        if self.mod_pending:
            self.mod_plan = None
            self.mod_pending = False
            return True
        return False

    @staticmethod
    def normalise_route_spine(
        route: List[str], origin: Optional[str] = None, destination: Optional[str] = None
    ) -> List[str]:
        items = [str(x).strip().upper() for x in (route or []) if str(x).strip()]
        if origin:
            items = [x for x in items if x != origin]
            items.insert(0, origin)
        if destination:
            items = [x for x in items if x != destination]
            items.append(destination)
        compact = []
        for fix in items:
            if not compact or compact[-1] != fix:
                compact.append(fix)
        return compact

    def parse_route_string(
        self, origin: str, destination: str, route_text: str
    ) -> Tuple[List[str], Dict[str, str]]:
        origin, destination = origin.strip().upper(), destination.strip().upper()
        tokens = [t.strip().upper() for t in route_text.split() if t.strip()]
        fixes: List[str] = [origin]
        via_by_fix: Dict[str, str] = {}
        pending_via = "DCT"

        for token in tokens:
            if token in ("DCT", "DIRECT"):
                pending_via = "DCT"
                continue
            if re.fullmatch(r"[A-Z]{1,3}[0-9]{1,4}[A-Z]?", token):
                pending_via = token
                continue
            if not re.fullmatch(r"[A-Z0-9]{2,7}", token) or token in (origin, destination):
                continue
            if fixes[-1] == token:
                continue

            if pending_via != "DCT":
                airway_path = self.navdb.find_airway_path(pending_via, fixes[-1], token)
                if airway_path:
                    for fix in airway_path[1:]:
                        if fixes[-1] != fix:
                            fixes.append(fix)
                            via_by_fix[fix] = pending_via
                else:
                    if fixes[-1] != "--- DISCONTINUITY ---":
                        fixes.append("--- DISCONTINUITY ---")
                    fixes.append(token)
                    via_by_fix[token] = "DCT"
            else:
                fixes.append(token)
                via_by_fix[token] = "DCT"
            pending_via = "DCT"

        if fixes[-1] != destination:
            fixes.append(destination)
            via_by_fix[destination] = "DCT"
        return fixes, via_by_fix

    def stage_route_entry(self, origin: str, destination: str, route_text: str = "") -> List[str]:
        origin, destination = origin.strip().upper(), destination.strip().upper()
        buf = self.ensure_mod_buffer()
        endpoints_changed = buf.origin != origin or buf.destination != destination

        if route_text.strip():
            new_spine, parsed_via = self.parse_route_string(origin, destination, route_text)
        elif not endpoints_changed:
            new_spine = self.normalise_route_spine(buf.route_base, origin, destination)
            parsed_via = dict(buf.route_via)
        else:
            new_spine, parsed_via = [origin, destination], {}

        route_changed = new_spine != buf.route_base
        buf.origin = origin
        buf.destination = destination
        buf.route_base = list(new_spine)
        buf.route_via = parsed_via

        if endpoints_changed or route_changed:
            buf.procedures = {}
            buf.expanded_route = list(new_spine)
        else:
            buf.expanded_route, _ = self.build_procedure_route(buf.route_base, buf.procedures)
        return buf.expanded_route

    def build_procedure_route(
        self, base_route: List[str], procedures: Dict[str, Dict[str, Any]]
    ) -> Tuple[List[str], List[str]]:
        route = list(base_route)
        if len(route) < 2:
            return route, []
        notes: List[str] = []
        dep = procedures.get("departures", {}) if procedures else {}
        arr = procedures.get("arrivals", {}) if procedures else {}

        # 1. Insert SID or Runway/No-SID synthetic climb
        dep_sid = dep.get("sid") if dep else None
        if dep_sid and dep_sid not in ("NO SID", "NONE", "NONE SELECTED", "------"):
            airport = dep.get("airport", route[0]).upper()
            leg_data = self.navdb.get_procedure_fixes(
                airport, "SID", dep_sid, dep.get("runway"), dep.get("sid_transition")
            )
            used_fallback = False
            if not leg_data and dep.get("sid_transition"):
                leg_data = self.navdb.get_procedure_fixes(
                    airport, "SID", dep_sid, dep.get("runway"), None
                )
                used_fallback = bool(leg_data)
            fixes = [f[0] for f in leg_data]
            if fixes:
                fixes = [f for f in fixes if f not in (route[0], route[-1]) and f not in route[1:-1]]
                enroute_first = route[1] if len(route) > 2 else route[-1]
                needs_discon = (
                    len(fixes) > 0 and enroute_first != fixes[-1] and enroute_first != route[-1]
                )
                if fixes[-1] == "(VECT)":
                    needs_discon = True

                inserted = fixes + (["--- DISCONTINUITY ---"] if needs_discon else [])
                route = [route[0]] + inserted + route[1:]
                suffix = " (transition fallback)" if used_fallback else ""
                notes.append(f"SID {dep_sid}: {len(fixes)} leg(s) inserted{suffix}")
            else:
                notes.append(f"SID {dep_sid}: no matching CIFP leg records")
        elif dep:
            # NO SID explicitly selected: runway heading climb to (1500) + DISCONTINUITY
            inserted = ["(1500)", "--- DISCONTINUITY ---"]
            if len(route) > 1 and route[1] != "(1500)":
                route = [route[0]] + inserted + route[1:]
                notes.append("NO SID: (1500) CLIMB & DISCONTINUITY INSERTED")

        # 2. Insert STAR fixes
        if arr.get("star"):
            airport = arr.get("airport", route[-1]).upper()
            leg_data = self.navdb.get_procedure_fixes(
                airport, "STAR", arr["star"], transition=arr.get("star_transition")
            )
            used_fallback = False
            if not leg_data and arr.get("star_transition"):
                leg_data = self.navdb.get_procedure_fixes(airport, "STAR", arr["star"], transition=None)
                used_fallback = bool(leg_data)
            fixes = [f[0] for f in leg_data]
            if fixes:
                fixes = [f for f in fixes if f not in (route[0], route[-1]) and f not in route[1:-1]]
                enroute_last = route[-2] if len(route) > 2 else route[0]
                needs_discon = (
                    len(fixes) > 0
                    and enroute_last != fixes[0]
                    and enroute_last != "--- DISCONTINUITY ---"
                )
                inserted = (["--- DISCONTINUITY ---"] if needs_discon else []) + fixes
                route = route[:-1] + inserted + [route[-1]]
                suffix = " (transition fallback)" if used_fallback else ""
                notes.append(f"STAR {arr['star']}: {len(fixes)} leg(s) inserted{suffix}")
            else:
                notes.append(f"STAR {arr['star']}: no matching CIFP leg records")

        # 3. Insert Approach fixes
        if arr.get("approach"):
            airport = arr.get("airport", route[-1]).upper()
            leg_data = self.navdb.get_procedure_fixes(
                airport, "APPCH", arr["approach"], transition=arr.get("approach_transition")
            )
            fixes = [f[0] for f in leg_data]
            if fixes:
                fixes = [f for f in fixes if f not in (route[0], route[-1]) and f not in route[1:-1]]
                route = route[:-1] + fixes + [route[-1]]
                notes.append(f"APP {arr['approach']}: {len(fixes)} leg(s) inserted")
            else:
                notes.append(f"APP {arr['approach']}: no matching transition leg records")

        compact = []
        for fix in route:
            if not compact or compact[-1] != fix:
                compact.append(fix)
        return compact, notes

    def stage_procedures(
        self, kind: str, selection: Dict[str, Any]
    ) -> Tuple[List[str], List[str]]:
        buf = self.ensure_mod_buffer()
        buf.route_base = self.normalise_route_spine(buf.route_base, buf.origin, buf.destination)
        buf.procedures[kind] = selection
        expanded, notes = self.build_procedure_route(buf.route_base, buf.procedures)
        buf.expanded_route = expanded
        return expanded, notes

    def stage_flight_number(self, value: str) -> bool:
        val = (value or "").strip().upper()
        if val == self.act_plan.flight_number:
            if self.mod_plan and self.mod_plan.flight_number == val:
                self.mod_plan.flight_number = self.act_plan.flight_number
            return False
        buf = self.ensure_mod_buffer()
        buf.flight_number = val
        return True

    def insert_waypoint_after(self, index: int, fix: str) -> int:
        fix = fix.strip().upper()
        buf = self.ensure_mod_buffer()
        route = list(buf.expanded_route)
        idx = max(0, min(index, len(route) - 1))
        if fix in route:
            raise ValueError(f"{fix} is already in this route.")
        route.insert(idx + 1, fix)
        buf.route_base = self.normalise_route_spine(route, route[0], route[-1])
        buf.expanded_route = list(buf.route_base)
        return idx + 1

    def insert_airway_after(self, index: int, airway: str, exit_fix: str) -> int:
        airway, exit_fix = airway.strip().upper(), exit_fix.strip().upper()
        buf = self.ensure_mod_buffer()
        route = list(buf.expanded_route)
        idx = max(0, min(index, len(route) - 1))
        start_fix = route[idx]
        path = self.navdb.find_airway_path(airway, start_fix, exit_fix)
        if not path:
            raise ValueError(f"No connected {airway} path found from {start_fix} to {exit_fix}.")

        new_route = route[: idx + 1] + path[1:] + route[idx + 1 :]
        seen, deduped = set(), []
        for item in new_route:
            if item not in seen:
                deduped.append(item)
                seen.add(item)
        buf.route_base = self.normalise_route_spine(deduped, deduped[0], deduped[-1])
        buf.expanded_route = list(buf.route_base)
        for item in path[1:]:
            buf.route_via[item] = airway
        return min(idx + 1, len(buf.expanded_route) - 1)

    def delete_discontinuity_at(self, index: int) -> bool:
        buf = self.ensure_mod_buffer()
        if 0 <= index < len(buf.expanded_route):
            if buf.expanded_route[index] == "--- DISCONTINUITY ---":
                buf.expanded_route.pop(index)
                buf.route_base = [f for f in buf.route_base if f != "--- DISCONTINUITY ---"]
                return True
        return False

    def delete_waypoint_at(self, index: int) -> str:
        buf = self.ensure_mod_buffer()
        route = list(buf.expanded_route)
        if len(route) <= 2 or index in (0, len(route) - 1):
            raise ValueError("Origin and destination cannot be deleted.")
        removed = route.pop(index)
        buf.expanded_route = route
        buf.route_base = [f for f in buf.route_base if f != removed]
        return removed

    def direct_to(self, fix: str):
        fix = fix.strip().upper()
        buf = self.ensure_mod_buffer()
        route = list(buf.expanded_route)
        if fix not in route:
            route.append(fix)
        else:
            route = route[route.index(fix) :]
        buf.expanded_route = route
        buf.route_base = list(route)

    @staticmethod
    def great_circle_nm(
        a: Optional[Tuple[float, float]], b: Optional[Tuple[float, float]]
    ) -> Optional[float]:
        if not a or not b:
            return None
        radius_nm = 3440.065
        lat1, lon1 = map(math.radians, a)
        lat2, lon2 = map(math.radians, b)
        dlat, dlon = lat2 - lat1, lon2 - lon1
        h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 2 * radius_nm * math.asin(min(1.0, math.sqrt(h)))

    @staticmethod
    def utc_at_waypoint(
        departure_hhmm: str, cumulative_nm: Optional[float], groundspeed: int
    ) -> Optional[str]:
        if not departure_hhmm or cumulative_nm is None or not 50 <= int(groundspeed) <= 700:
            return None
        value = str(departure_hhmm).replace(":", "")
        if not re.fullmatch(r"\d{4}", value):
            return None
        hh, mm = int(value[:2]), int(value[2:])
        if hh > 23 or mm > 59:
            return None
        elapsed = cumulative_nm / int(groundspeed) * 60.0
        total = (hh * 60 + mm + int(round(elapsed))) % 1440
        return f"{total // 60:02d}:{total % 60:02d}Z"

    @staticmethod
    def fuel_at_waypoint(
        cumulative_nm: Optional[float],
        groundspeed: int,
        departure_fob: Optional[float],
        actual_flow: Optional[float],
        planned_departure_fob: Optional[float],
        planned_flow: Optional[float],
    ) -> Optional[Tuple[float, float]]:
        values = (departure_fob, actual_flow, planned_departure_fob, planned_flow)
        if cumulative_nm is None or any(v is None for v in values):
            return None
        elapsed_hours = cumulative_nm / int(groundspeed)
        estimated = float(departure_fob) - float(actual_flow) * elapsed_hours
        planned = float(planned_departure_fob) - float(planned_flow) * elapsed_hours
        return estimated, estimated - planned

    def fetch_simbrief_plan(self, identity: str) -> Dict[str, Any]:
        identity = (identity or "").strip()
        if not identity:
            raise ValueError("Enter a SimBrief username or Pilot ID.")
        query = urllib.parse.urlencode(
            {"userid": identity, "json": "1"}
            if identity.isdigit()
            else {"username": identity, "json": "1"}
        )
        url = "https://www.simbrief.com/api/xml.fetcher.php?" + query
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "A220-FMS-Touchscreen-Prototype/0.60",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = response.read(5_000_000).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise ValueError(
                f"SimBrief returned HTTP {exc.code}. Check the username/Pilot ID and ensure an OFP has been generated."
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ValueError(f"Could not contact SimBrief: {exc}") from exc

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "SimBrief returned an error or an unexpected response, not valid JSON."
            ) from exc

        if not isinstance(data, dict) or data.get("fetch") == "error" or data.get("error"):
            raise ValueError(
                str(
                    data.get(
                        "error",
                        "No flight plan was returned. Generate an OFP in SimBrief first.",
                    )
                )
                if isinstance(data, dict)
                else "Unexpected SimBrief response."
            )

        general = data.get("general") or {}
        origin = str(general.get("orig") or general.get("origin") or "").strip().upper()
        destination = str(general.get("dest") or general.get("destination") or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{4}", origin) or not re.fullmatch(r"[A-Z]{4}", destination):
            raise ValueError(
                "The returned plan is missing valid four-letter origin/destination identifiers."
            )

        navlog = data.get("navlog") or {}
        fixes = navlog.get("fix", []) if isinstance(navlog, dict) else []
        if isinstance(fixes, dict):
            fixes = [fixes]

        mids: List[str] = []
        route_via: Dict[str, str] = {}
        if isinstance(fixes, list):
            for item in fixes:
                if not isinstance(item, dict):
                    continue
                ident = (
                    str(item.get("ident") or item.get("fix") or item.get("name") or "")
                    .strip()
                    .upper()
                )
                if re.fullmatch(r"[A-Z0-9]{2,7}", ident) and ident not in (origin, destination):
                    if not mids or mids[-1] != ident:
                        mids.append(ident)
                    via = (
                        str(item.get("via") or item.get("airway") or item.get("route") or "")
                        .strip()
                        .upper()
                    )
                    if via and via not in ("DCT", "DIRECT", "DIRECT TO", "SID", "STAR"):
                        route_via[ident] = via
                    elif via in ("DCT", "DIRECT", "DIRECT TO"):
                        route_via[ident] = "DCT"

        if not mids:
            route_text = str(general.get("route") or "").strip().upper()
            parsed_fixes, parsed_via = self.parse_route_string(origin, destination, route_text)
            mids = parsed_fixes[1:-1]
            route_via = parsed_via

        route = [origin] + mids + [destination]
        flight_no = str(general.get("flight_number") or general.get("callsign") or "").strip().upper()

        buf = self.ensure_mod_buffer()
        buf.origin = origin
        buf.destination = destination
        buf.route_base = list(route)
        buf.expanded_route = list(route)
        buf.route_via = dict(route_via)
        buf.procedures = {}
        if flight_no:
            buf.flight_number = flight_no[:7]

        weights = data.get("weights") or {}
        fuel_data = data.get("fuel") or {}
        try:
            zfw = float(weights.get("est_zfw") or 42000.0)
            fob = float(fuel_data.get("plan_ramp") or 5000.0)
            taxi = float(fuel_data.get("taxi") or 150.0)
            buf.wb.zfw_kg = zfw
            wing_fuel = min(3050.0, fob / 2.0)
            ctr_fuel = max(0.0, fob - (wing_fuel * 2.0))
            buf.wb.fuel_plan = FuelPlan(
                left_main_kg=wing_fuel,
                right_main_kg=wing_fuel,
                center_kg=ctr_fuel,
                taxi_fuel_kg=taxi,
            )
        except (ValueError, TypeError):
            pass

        return {
            "origin": origin,
            "destination": destination,
            "route": route,
            "route_via": route_via,
            "route_text": str(general.get("route") or ""),
            "aircraft": str(general.get("aircraft") or ""),
            "flight_number": flight_no,
            "ofp_id": str(
                (data.get("params") or {}).get("request_id")
                or (data.get("general") or {}).get("release")
                or ""
            ),
        }

    def build_leg_objects(self) -> List[WaypointLeg]:
        buf = self.active_buffer()
        route = list(buf.expanded_route)
        dep = buf.procedures.get("departures", {}) if buf.procedures else {}
        arr = buf.procedures.get("arrivals", {}) if buf.procedures else {}

        sid_fixes, star_fixes, app_fixes = set(), set(), set()
        cifp_constraints: Dict[str, Tuple[str, str]] = {}

        if dep.get("sid") and route:
            dep_legs = self.navdb.get_procedure_fixes(
                dep.get("airport", route[0]),
                "SID",
                dep["sid"],
                dep.get("runway"),
                dep.get("sid_transition"),
            )
            for fix, alt_c, spd_c in dep_legs:
                sid_fixes.add(fix)
                if alt_c or spd_c:
                    cifp_constraints[fix] = (alt_c, spd_c)

        if arr.get("star") and route:
            star_legs = self.navdb.get_procedure_fixes(
                arr.get("airport", route[-1]),
                "STAR",
                arr["star"],
                transition=arr.get("star_transition"),
            )
            for fix, alt_c, spd_c in star_legs:
                star_fixes.add(fix)
                if alt_c or spd_c:
                    cifp_constraints[fix] = (alt_c, spd_c)

        if arr.get("approach") and route:
            app_legs = self.navdb.get_procedure_fixes(
                arr.get("airport", route[-1]),
                "APPCH",
                arr["approach"],
                transition=arr.get("approach_transition"),
            )
            for fix, alt_c, spd_c in app_legs:
                app_fixes.add(fix)
                if alt_c or spd_c:
                    cifp_constraints[fix] = (alt_c, spd_c)

        real_fixes = [f for f in route if f != "--- DISCONTINUITY ---" and not f.startswith("(")]
        coords = self.navdb.resolve_route_coordinates(real_fixes)
        legs: List[WaypointLeg] = []

        for i, fix in enumerate(route):
            if fix == "--- DISCONTINUITY ---":
                legs.append(WaypointLeg.create_discontinuity())
                continue

            if i == 0:
                seg = "ORIGIN"
            elif i == len(route) - 1:
                seg = "DEST"
            elif fix in app_fixes:
                seg = "APP"
            elif fix in star_fixes:
                seg = "STAR"
            elif fix in sid_fixes:
                seg = "SID"
            else:
                seg = str(buf.route_via.get(fix, "DCT")).upper() or "DCT"

            if fix in buf.holds:
                seg = "HOLD"

            if fix.startswith("(") and fix.endswith(")"):
                c_alt = fix.strip("()") if fix.strip("()").isdigit() else ""
                legs.append(WaypointLeg.create_synthetic(fix, seg, alt_constraint=c_alt))
                continue

            lat_lon = coords.get(fix)
            pilot_c = buf.constraints.get(fix, {})
            cifp_alt, cifp_spd = cifp_constraints.get(fix, ("", ""))

            alt_c = pilot_c.get("altitude") or cifp_alt
            spd_c = pilot_c.get("speed") or cifp_spd

            legs.append(
                WaypointLeg(
                    ident=fix,
                    via=seg,
                    segment_type=seg,
                    lat=lat_lon[0] if lat_lon else None,
                    lon=lat_lon[1] if lat_lon else None,
                    speed_constraint=spd_c,
                    alt_constraint=alt_c,
                    hold=buf.holds.get(fix),
                )
            )
        return legs

    def compute_vnav(self) -> Tuple[List[VnavPointPrediction], VnavProfileSummary]:
        buf = self.active_buffer()
        legs = self.build_leg_objects()
        distances: List[Optional[float]] = []
        for i, leg in enumerate(legs):
            if i == 0 or leg.is_discontinuity or leg.is_synthetic:
                distances.append(0.0)
            else:
                prev = legs[i - 1]
                if prev.is_discontinuity or prev.is_synthetic:
                    distances.append(0.0)
                else:
                    d = self.great_circle_nm(
                        (prev.lat, prev.lon) if prev.lat is not None else None,
                        (leg.lat, leg.lon) if leg.lat is not None else None,
                    )
                    distances.append(d)

        return VnavEngine.compute_profile(
            legs=legs,
            distances_nm=distances,
            crz_alt_str=buf.crz_alt,
            trans_alt_ft=self.trans_alt_ft,
            vpa_deg=self.vpa_deg,
        )