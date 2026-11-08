from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ==============================================================================
# A220-300 (BD-500-1A11) CERTIFICATED LIMITS (FCOM Vol 2 Ch 2 Limitations)
# ==============================================================================
MAX_RAMP_WEIGHT_KG = 60328
MAX_TAKEOFF_WEIGHT_KG = 59874
MAX_LANDING_WEIGHT_KG = 57379
MAX_ZERO_FUEL_WEIGHT_KG = 54658
MIN_FLIGHT_WEIGHT_KG = 36287

# Maximum Usable Fuel (kg) @ 0.809 kg/L density
MAX_FUEL_WING_TANK_KG = 3050.0
MAX_FUEL_CENTER_TANK_KG = 11300.0
MAX_TOTAL_FUEL_KG = 17400.0
MAX_FUEL_IMBALANCE_KG = 454.0
MIN_GO_AROUND_FUEL_PER_WING = 442.0

# Operational Envelopes
MAX_AIRPORT_PRESSURE_ALT_FT = 10000
MAX_OPERATING_ALTITUDE_FT = 41000
MAX_OAT_TAKEOFF_LANDING_C = 52.5
MIN_OAT_TAKEOFF_LANDING_C = -54.0
MAX_TAILWIND_TAKEOFF_KT = 10
MAX_TAILWIND_LANDING_KT = 10


@dataclass
class FuelPlan:
    """Detailed fuel quantity management per tank."""
    left_main_kg: float = 0.0
    right_main_kg: float = 0.0
    center_kg: float = 0.0
    taxi_fuel_kg: float = 150.0
    reserve_fuel_kg: float = 1200.0

    @property
    def total_fob_kg(self) -> float:
        return self.left_main_kg + self.right_main_kg + self.center_kg

    def validate(self) -> List[str]:
        warnings = []
        if self.left_main_kg > MAX_FUEL_WING_TANK_KG or self.right_main_kg > MAX_FUEL_WING_TANK_KG:
            warnings.append("WING TANK OVERFILL")
        if self.center_kg > MAX_FUEL_CENTER_TANK_KG:
            warnings.append("CTR TANK OVERFILL")
        if self.total_fob_kg > MAX_TOTAL_FUEL_KG:
            warnings.append("TOTAL FUEL EXCEEDS CAPACITY")

        imbalance = abs(self.left_main_kg - self.right_main_kg)
        if imbalance > MAX_FUEL_IMBALANCE_KG:
            warnings.append(f"FUEL IMBALANCE ({int(imbalance)} KG > {int(MAX_FUEL_IMBALANCE_KG)} KG)")

        if self.left_main_kg < MIN_GO_AROUND_FUEL_PER_WING or self.right_main_kg < MIN_GO_AROUND_FUEL_PER_WING:
            warnings.append("LOW FUEL FOR GO-AROUND")

        return warnings


@dataclass
class WeightAndBalance:
    """Manages gross weight calculations and structural limit validations."""
    zfw_kg: float = 42000.0
    fuel_plan: FuelPlan = field(default_factory=FuelPlan)
    cg_mac_percent: float = 25.0

    @property
    def gross_weight_kg(self) -> float:
        return self.zfw_kg + self.fuel_plan.total_fob_kg

    @property
    def takeoff_weight_kg(self) -> float:
        return max(self.gross_weight_kg - self.fuel_plan.taxi_fuel_kg, MIN_FLIGHT_WEIGHT_KG)

    def validate_weights(self) -> List[str]:
        """Returns warnings if any certificated A220 structural limit is breached."""
        warnings = []
        if self.zfw_kg > MAX_ZERO_FUEL_WEIGHT_KG:
            warnings.append(f"ZFW EXCEEDS {MAX_ZERO_FUEL_WEIGHT_KG} KG")
        if self.gross_weight_kg > MAX_RAMP_WEIGHT_KG:
            warnings.append(f"RAMP WT EXCEEDS {MAX_RAMP_WEIGHT_KG} KG")
        if self.takeoff_weight_kg > MAX_TAKEOFF_WEIGHT_KG:
            warnings.append(f"TOW EXCEEDS {MAX_TAKEOFF_WEIGHT_KG} KG")
        if self.zfw_kg < MIN_FLIGHT_WEIGHT_KG:
            warnings.append("ZFW BELOW MIN FLIGHT WT")

        warnings.extend(self.fuel_plan.validate())
        return warnings


@dataclass
class TakeoffPerformance:
    """Takeoff thrust setup, V-speed prediction, and dispatch validations."""
    thrust_mode: str = "TO"  # "TO", "TO-1", "TO-2", "TO-3"
    flex_temp_c: Optional[int] = None
    flap_setting: int = 2    # Standard A220 takeoff flaps: 2, 3, or 4
    apu_bleed_on: bool = False
    oat_c: float = 15.0
    headwind_kt: float = 0.0
    crosswind_kt: float = 0.0
    runway_condition_code: int = 6  # 6=Dry, 5=Good/Wet, 3=Medium, 1=Poor (RCAM)
    
    # Speeds (Computed or pilot-entered)
    v1: Optional[int] = None
    vr: Optional[int] = None
    v2: Optional[int] = None

    def calculate_baseline_vspeeds(self, tow_kg: float) -> Tuple[int, int, int]:
        """Estimates nominal V1, VR, V2 based on A220-300 weight and flap setting."""
        tow_t = tow_kg / 1000.0

        flap_offsets = {
            2: {"v1": 0.0, "vr": 0.0, "v2": 0.0},
            3: {"v1": -2.0, "vr": -2.0, "v2": -3.0},
            4: {"v1": -4.0, "vr": -5.0, "v2": -6.0},
        }
        offsets = flap_offsets.get(self.flap_setting, flap_offsets[2])

        v1_base = 92.0 + 0.85 * (tow_t - 36.0) + offsets["v1"]
        vr_base = 95.0 + 0.90 * (tow_t - 36.0) + offsets["vr"]
        v2_base = 108.0 + 0.98 * (tow_t - 36.0) + offsets["v2"]

        v1 = int(round(max(105, min(165, v1_base))))
        vr = int(round(max(v1, min(170, vr_base))))
        v2 = int(round(max(vr + 3, min(175, v2_base))))

        self.v1, self.vr, self.v2 = v1, vr, v2
        return v1, vr, v2

    def validate_takeoff_config(self, tow_kg: float) -> List[str]:
        warnings = []

        if self.flex_temp_c is not None and self.thrust_mode in ("TO-1", "TO-2", "TO-3"):
            warnings.append("DERATE + FLEX PROHIBITED")

        if self.flex_temp_c is not None and self.flex_temp_c <= self.oat_c:
            warnings.append("FLEX TEMP MUST BE HIGHER THAN OAT")

        if self.apu_bleed_on and self.oat_c <= 15.0:
            warnings.append("APU BLEED PROHIBITED (OAT <= 15°C)")

        if not (MIN_OAT_TAKEOFF_LANDING_C <= self.oat_c <= MAX_OAT_TAKEOFF_LANDING_C):
            warnings.append(f"OAT OUT OF ENVELOPE ({self.oat_c}°C)")

        if self.headwind_kt < -MAX_TAILWIND_TAKEOFF_KT:
            warnings.append(f"TAILWIND EXCEEDS {MAX_TAILWIND_TAKEOFF_KT} KT")

        max_xwind = {6: 32, 5: 32, 4: 27, 3: 20, 2: 10, 1: 10}.get(self.runway_condition_code, 32)
        if abs(self.crosswind_kt) > max_xwind:
            warnings.append(f"XWIND EXCEEDS RCAM LIMIT ({max_xwind} KT)")

        if self.flap_setting not in (2, 3, 4):
            warnings.append("INVALID TO FLAP CONFIG (USE 2, 3, 4)")

        if not all([self.v1, self.vr, self.v2]):
            warnings.append("VSPEEDS NOT SET")
        elif self.v1 and self.vr and self.v2:
            if not (self.v1 <= self.vr <= self.v2):
                warnings.append("VSPEED SEQUENCE INVALID (V1 <= VR <= V2)")

        return warnings


@dataclass
class LandingPerformance:
    """Calculates VREF, VAC, VGA, and VAPP per FCOM Vol 2 Section 3.3.7."""
    flap_setting: int = 4  # Normal: 4, Short field: 5
    headwind_kt: int = 0
    gust_kt: int = 0
    autothrottle_engaged: bool = True
    icing_conditions: bool = False
    vref: Optional[int] = None
    vac: Optional[int] = None
    vga: Optional[int] = None

    def calculate_reference_speeds(self, ldw_kg: float) -> Tuple[int, int, int]:
        """Estimates baseline VREF, VAC, and VGA."""
        ldw_t = ldw_kg / 1000.0
        offset = -4.0 if self.flap_setting == 5 else 0.0

        vref_base = 100.0 + 0.62 * (ldw_t - 36.0) + offset
        vref = int(round(max(110, min(155, vref_base))))
        vac = vref + 18
        vga = vref + 22

        self.vref, self.vac, self.vga = vref, vac, vga
        return vref, vac, vga

    def calculate_vapp(self, ldw_kg: float) -> Tuple[int, int]:
        """Calculates final VAPP and the applied Speed Adder."""
        if self.vref is None:
            self.calculate_reference_speeds(ldw_kg)

        base_vref = self.vref or 120
        speed_adder = 0
        gust_component = max(0, self.gust_kt - self.headwind_kt)
        half_gust = gust_component // 2

        if self.autothrottle_engaged:
            adder = 5 + min(half_gust, 5)
            speed_adder = min(adder, 10)
        else:
            speed_adder = min(half_gust, 10)

        vapp = base_vref + speed_adder

        if self.icing_conditions and self.flap_setting == 5:
            vapp += 5

        return vapp, speed_adder

    def validate_landing_config(self, ldw_kg: float) -> List[str]:
        warnings = []
        if ldw_kg > MAX_LANDING_WEIGHT_KG:
            warnings.append(f"LDW EXCEEDS {MAX_LANDING_WEIGHT_KG} KG")
        if self.flap_setting not in (4, 5):
            warnings.append("INVALID LANDING FLAP (USE 4 OR 5)")
        if self.headwind_kt < -MAX_TAILWIND_LANDING_KT:
            warnings.append(f"TAILWIND EXCEEDS {MAX_TAILWIND_LANDING_KT} KT")
        return warnings