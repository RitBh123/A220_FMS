from dataclasses import dataclass
from typing import List, Optional, Tuple
import math
import re


@dataclass
class VnavPointPrediction:
    ident: str
    cumulative_nm: float
    leg_nm: float
    phase: str  # CLB, CRZ, DES, APP, --
    target_spd: str
    target_alt_ft: int
    alt_formatted: str
    is_constraint: bool
    vpa_deg: Optional[float]
    rnp_nm: float


@dataclass
class VnavProfileSummary:
    crz_alt_ft: int
    toc_nm: Optional[float]
    tod_nm: Optional[float]
    dist_to_tod_nm: Optional[float]
    total_dist_nm: float
    default_vpa_deg: float = -3.0


class VnavEngine:
    """Pure Python A220 VNAV Path & Constraint Solver."""

    SPD_LIMIT_ALT_FT = 10000
    SPD_LIMIT_KIAS = 250
    DEFAULT_CLB_SPD = "290/.78"
    DEFAULT_CRZ_SPD = "M.78"
    DEFAULT_DES_SPD = ".78/290"
    DEFAULT_VPA_DEG = -3.0
    CLIMB_GRADIENT_FT_PER_NM = 330.0

    @staticmethod
    def parse_altitude_ft(text: str) -> Tuple[Optional[int], str]:
        raw = (text or "").strip().upper()
        if not raw:
            return None, "NONE"
        m_fl = re.fullmatch(r"FL\s*(\d{2,3})([+-]?)", raw)
        if m_fl:
            ft = int(m_fl.group(1)) * 100
            suffix = m_fl.group(2)
            ctype = "AT_OR_ABOVE" if suffix == "+" else ("AT_OR_BELOW" if suffix == "-" else "AT")
            return ft, ctype

        m_ft = re.fullmatch(r"(\d{3,5})([+-]?)", raw)
        if m_ft:
            ft = int(m_ft.group(1))
            suffix = m_ft.group(2)
            ctype = "AT_OR_ABOVE" if suffix == "+" else ("AT_OR_BELOW" if suffix == "-" else "AT")
            return ft, ctype

        return None, "NONE"

    @staticmethod
    def format_altitude(alt_ft: int, trans_alt_ft: int = 18000, suffix: str = "") -> str:
        if alt_ft >= trans_alt_ft:
            return f"FL{int(round(alt_ft / 100.0)):03d}{suffix}"
        return f"{int(round(alt_ft / 10.0) * 10)}{suffix}"

    @staticmethod
    def ft_per_nm_for_vpa(vpa_deg: float) -> float:
        ft_per_nm = 6076.11549
        return math.tan(math.radians(abs(vpa_deg))) * ft_per_nm

    @classmethod
    def compute_profile(
        cls,
        legs: List,
        distances_nm: List[Optional[float]],
        crz_alt_str: str = "FL230",
        trans_alt_ft: int = 18000,
        vpa_deg: float = -3.0,
        origin_elev_ft: int = 200,
        dest_elev_ft: int = 50,
    ) -> Tuple[List[VnavPointPrediction], VnavProfileSummary]:
        crz_ft, _ = cls.parse_altitude_ft(crz_alt_str)
        if not crz_ft:
            crz_ft = 23000

        n = len(legs)
        if n == 0:
            return [], VnavProfileSummary(crz_ft, None, None, None, 0.0, vpa_deg)

        # 1. Cumulative distances (Discontinuities and Synthetics add 0 distance)
        cum_dist: List[float] = [0.0] * n
        for i in range(1, n):
            if getattr(legs[i], "is_discontinuity", False) or getattr(legs[i], "is_synthetic", False):
                cum_dist[i] = cum_dist[i - 1]
            else:
                d = distances_nm[i] if (i < len(distances_nm) and distances_nm[i] is not None) else 8.0
                cum_dist[i] = cum_dist[i - 1] + d
        total_dist = cum_dist[-1]

        # 2. Forward Pass (Climb Profile)
        clb_alt: List[float] = [0.0] * n
        clb_alt[0] = float(origin_elev_ft)
        toc_nm: Optional[float] = None

        for i in range(1, n):
            if getattr(legs[i], "is_discontinuity", False):
                clb_alt[i] = clb_alt[i - 1]
                continue
            seg_d = cum_dist[i] - cum_dist[i - 1]
            unconstrained = clb_alt[i - 1] + seg_d * cls.CLIMB_GRADIENT_FT_PER_NM
            c_ft, c_type = cls.parse_altitude_ft(getattr(legs[i], "alt_constraint", ""))

            if c_ft is not None and i < n // 2 + 1:
                if c_type in ("AT", "AT_OR_BELOW"):
                    unconstrained = min(unconstrained, float(c_ft))
                elif c_type == "AT_OR_ABOVE":
                    unconstrained = max(unconstrained, float(c_ft))

            if unconstrained >= crz_ft:
                if toc_nm is None and clb_alt[i - 1] < crz_ft:
                    needed_ft = crz_ft - clb_alt[i - 1]
                    toc_nm = cum_dist[i - 1] + (needed_ft / cls.CLIMB_GRADIENT_FT_PER_NM)
                clb_alt[i] = float(crz_ft)
            else:
                clb_alt[i] = unconstrained

        # 3. Backward Pass (Geometric VPATH Descent from Destination/Runway)
        des_rate_ft_nm = cls.ft_per_nm_for_vpa(vpa_deg)
        des_alt: List[float] = [0.0] * n
        des_alt[-1] = float(dest_elev_ft)

        start_idx = n - 2
        if n >= 2 and re.fullmatch(r"RW\d{2}[LCR]?", str(legs[-2].ident)):
            des_alt[-2] = float(dest_elev_ft + 50)
            start_idx = n - 3

        for i in range(start_idx, -1, -1):
            if getattr(legs[i], "is_discontinuity", False):
                des_alt[i] = des_alt[i + 1]
                continue
            seg_d = cum_dist[i + 1] - cum_dist[i]
            proj_alt = des_alt[i + 1] + seg_d * des_rate_ft_nm
            c_ft, c_type = cls.parse_altitude_ft(getattr(legs[i], "alt_constraint", ""))

            if c_ft is not None and i >= n // 2:
                if c_type == "AT":
                    proj_alt = float(c_ft)
                elif c_type == "AT_OR_ABOVE":
                    proj_alt = max(proj_alt, float(c_ft))
                elif c_type == "AT_OR_BELOW":
                    proj_alt = min(proj_alt, float(c_ft))

            des_alt[i] = min(float(crz_ft), proj_alt)

        tod_nm: Optional[float] = None
        for i in range(n - 2, -1, -1):
            if des_alt[i] >= crz_ft and des_alt[i + 1] < crz_ft:
                needed_ft = crz_ft - des_alt[i + 1]
                tod_nm = max(0.0, cum_dist[i + 1] - (needed_ft / des_rate_ft_nm))
                break

        # 4. Merge Predictions
        predictions: List[VnavPointPrediction] = []
        last_valid_alt_ft = origin_elev_ft

        for i, leg in enumerate(legs):
            if getattr(leg, "is_discontinuity", False):
                predictions.append(
                    VnavPointPrediction(
                        ident=leg.ident,
                        cumulative_nm=cum_dist[i],
                        leg_nm=0.0,
                        phase="--",
                        target_spd="--",
                        target_alt_ft=last_valid_alt_ft,
                        alt_formatted="--",
                        is_constraint=False,
                        vpa_deg=None,
                        rnp_nm=0.0,
                    )
                )
                continue

            if getattr(leg, "is_synthetic", False):
                raw_ident = str(leg.ident).strip("()")
                if raw_ident.isdigit():
                    tgt_alt = int(raw_ident)
                    alt_fmt = cls.format_altitude(tgt_alt, trans_alt_ft)
                    is_c = True
                else:
                    tgt_alt = last_valid_alt_ft
                    alt_fmt = "--"
                    is_c = False

                predictions.append(
                    VnavPointPrediction(
                        ident=leg.ident,
                        cumulative_nm=cum_dist[i],
                        leg_nm=0.0,
                        phase=getattr(leg, "segment_type", "SID"),
                        target_spd="--",
                        target_alt_ft=tgt_alt,
                        alt_formatted=alt_fmt,
                        is_constraint=is_c,
                        vpa_deg=None,
                        rnp_nm=1.0,
                    )
                )
                continue

            seg_d = cum_dist[i] - cum_dist[i - 1] if i > 0 else 0.0
            c_ft, c_type = cls.parse_altitude_ft(getattr(leg, "alt_constraint", ""))
            has_c = c_ft is not None

            if i == 0:
                phase = "CLB"
                alt_ft = int(round(clb_alt[i]))
            elif i == n - 1:
                phase = "APP"
                alt_ft = int(round(des_alt[i]))
            elif clb_alt[i] < crz_ft and clb_alt[i] <= des_alt[i]:
                phase = "CLB"
                alt_ft = int(round(clb_alt[i]))
            elif des_alt[i] < crz_ft:
                phase = "APP" if leg.segment_type == "APP" else "DES"
                alt_ft = int(round(des_alt[i]))
            else:
                phase = "CRZ"
                alt_ft = crz_ft

            leg_vpa: Optional[float] = None
            if i > 0 and phase in ("DES", "APP") and seg_d > 0.05:
                dh = alt_ft - last_valid_alt_ft
                if dh < 0:
                    leg_vpa = -math.degrees(math.atan2(abs(dh), seg_d * 6076.11549))

            last_valid_alt_ft = alt_ft

            if getattr(leg, "speed_constraint", ""):
                spd_str = leg.speed_constraint
            elif i == 0:
                spd_str = "V2+10"
            elif i == n - 1 or re.fullmatch(r"RW\d{2}[LCR]?", str(leg.ident)):
                spd_str = "VAPP"
            elif alt_ft <= cls.SPD_LIMIT_ALT_FT:
                spd_str = str(cls.SPD_LIMIT_KIAS)
            elif phase == "CLB":
                spd_str = "290" if alt_ft < 28000 else "M.78"
            elif phase == "CRZ":
                spd_str = cls.DEFAULT_CRZ_SPD
            else:
                spd_str = "290" if alt_ft < 28000 else "M.78"

            if leg.segment_type == "APP" or i == n - 1:
                rnp = 0.3
            elif leg.segment_type in ("SID", "STAR") or i == 0:
                rnp = 1.0
            else:
                rnp = 2.0

            suffix = "+" if c_type == "AT_OR_ABOVE" else ("-" if c_type == "AT_OR_BELOW" else "")
            alt_fmt = cls.format_altitude(alt_ft, trans_alt_ft, suffix if has_c else "")

            predictions.append(
                VnavPointPrediction(
                    ident=leg.ident,
                    cumulative_nm=cum_dist[i],
                    leg_nm=seg_d,
                    phase=phase,
                    target_spd=spd_str,
                    target_alt_ft=alt_ft,
                    alt_formatted=alt_fmt,
                    is_constraint=has_c or bool(getattr(leg, "speed_constraint", "")),
                    vpa_deg=leg_vpa,
                    rnp_nm=rnp,
                )
            )

        summary = VnavProfileSummary(
            crz_alt_ft=crz_ft,
            toc_nm=toc_nm,
            tod_nm=tod_nm,
            dist_to_tod_nm=tod_nm,
            total_dist_nm=total_dist,
            default_vpa_deg=vpa_deg,
        )
        return predictions, summary