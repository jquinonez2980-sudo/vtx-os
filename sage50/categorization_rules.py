"""
Concetta Enterprises Inc. — Bank Transaction Categorization Ruleset
Generated: 2026-05-09
Purpose: Replace generic Canadian payee ruleset with client-specific rules
Expected Impact: ~85% auto-categorization, 5–10% manual review queue
"""

from decimal import Decimal
from typing import Tuple, Optional

# Known cheque payees populated after live runs confirm payee names.
# Format: (UPPERCASE_KEYWORD_IN_DESCRIPTION, gl_no, account_name)
# Example: ("ROGERS COMMUNICATIONS", 5600, "Telephone & Cellular")
# Leave empty — entries are added as payees are confirmed from cheque OCR.
_CHEQUE_PAYEES: list[tuple[str, int, str]] = []


# Maps our internal 4-digit GL codes to Concetta's actual Sage 50 8-digit lId values.
# Applied only at bridge post time; categorization and BQ use the 4-digit codes.
CONCETTA_ACCOUNT_MAP: dict[str, str] = {
    "1060": "11000000",   # Bank (Sage 50: "Bank" lId=11000000)
    "2100": "21000000",   # Employee Tax Deductions
    "4100": "40500000",   # Revenue → Mortgage Interest (only revenue posting account)
    "5155": "51550000",   # Car Lease
    "5200": "52000000",   # Bank Charges & Interest
    "5400": "54000000",   # Insurance
    "5600": "56000000",   # Telephone & Cellular
    "5700": "57000000",   # Visa
    "5725": "57250000",   # AMEX
    "5750": "57500000",   # Mastercard
    "5800": "58000000",   # Rent
    "5850": "58500000",   # Wages & Benefits
    "5900": "59000000",   # Suspense
}


class ConcettaRuleset:
    """
    Client-specific categorization rules for Concetta Enterprises Inc.
    
    Rules are applied in priority order:
    0. Revenue (SENTRIX FINANCI deposits) → 4100
    1. Insurance (ECONOMICAL INS) → 5400
    2. Bank Fees → 5200
    3. Telecom (FIDO) → 5600
    4. Credit Card Clearing (TD VISA, SCOTIA VISA, PC MASTRCRD, AMEX CARDS) → 5700/5750
    5. Payroll Tax (RECEIVER GENERAL, CRA) → 2100
    6. Payroll/Wages (CONCETTA BOSH, CHRISTINA BOSH) → 5850
    7. Car Lease (SPL LOAN) → 5155
    8. Rent → 5800
    9. Catch-all Suspense → 5900
    """
    
    def __init__(self):
        """Initialize the ruleset with all categorization rules."""
        self.rules = [
            self._rule_revenue,
            self._rule_cheque_payee,   # payee names extracted from cheque images
            self._rule_insurance,
            self._rule_bank_fees,
            self._rule_telecom,
            self._rule_card_clearing,
            self._rule_payroll_tax,
            self._rule_payroll_wages,
            self._rule_car_lease,
            self._rule_rent,
            self._rule_suspense_fallback,
        ]
    
    def categorize(self, description: str, amount: Decimal) -> Tuple[int, str, Decimal]:
        """
        Apply rules in priority order. First match wins.
        
        Args:
            description: Transaction description from bank statement
            amount: Transaction amount (positive = deposit, negative = withdrawal)
        
        Returns:
            Tuple of (gl_account_number, account_name, confidence_score)
        """
        desc_upper = description.upper()
        
        for rule in self.rules:
            result = rule(desc_upper, amount)
            if result is not None:
                return result
        
        # Fallback (should never reach here due to suspense rule)
        return (5900, "Suspense", Decimal("0"))
    
    # ========== RULE 0: Revenue (client invoice payments) ==========
    def _rule_revenue(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """SENTRIX FINANCI (client invoice deposits) → 4100 Revenue, 95% confidence"""
        if "SENTRIX FINANCI" in desc and amount > 0:
            return (4100, "Revenue", Decimal("95"))
        return None

    # ========== RULE 1: Cheque payees (populated after first live run) ==========
    def _rule_cheque_payee(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """Match CHQ transactions where payee was extracted from cheque image.

        desc format after enrichment: "CHQ#00788 - Rogers Communications Inc."
        Add entries to _CHEQUE_PAYEES as payees are confirmed from live runs.
        """
        for keyword, gl_no, gl_name in _CHEQUE_PAYEES:
            if keyword in desc:
                return (gl_no, gl_name, Decimal("85"))
        return None

    # ========== RULE 2: Insurance ==========
    def _rule_insurance(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """ECONOMICAL INS → 5400 Insurance, 95% confidence"""
        if "ECONOMICAL INS" in desc:
            return (5400, "Insurance", Decimal("95"))
        return None
    
    # ========== RULE 2: Bank Fees ==========
    def _rule_bank_fees(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """
        Bank Charges & Fees → 5200, 90% confidence
        Match: MONTHY PLAN FEE, PAPER STMT FEE, NSF RETURN FEE, CHEQUE CHARGE, BANK CHARGE
        """
        bank_fee_keywords = [
            "MONTHLY PLAN FEE",
            "PAPER STMT FEE",
            "NSF RETURN FEE",
            "CHEQUE CHARGE",
            "BANK CHARGE",
        ]
        for keyword in bank_fee_keywords:
            if keyword in desc:
                return (5200, "Bank Charges & Interest", Decimal("90"))
        return None
    
    # ========== RULE 3: Telecom ==========
    def _rule_telecom(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """FIDO / FIDO SOLUTN → 5600 Telephone & Cellular, 98% confidence"""
        if "FIDO" in desc:
            return (5600, "Telephone & Cellular", Decimal("98"))
        return None
    
    # ========== RULE 4: Credit Card Clearing ==========
    def _rule_card_clearing(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """
        Credit card payment clearing entries → 5700 or 5750, 98% confidence
        Match: TD VISA, SCOTIA VISA → 5700 (Visa); PC MASTRCRD, AMEX CARDS → 5750
        """
        if "TD VISA" in desc or "SCOTIA VISA" in desc:
            return (5700, "Visa", Decimal("98"))
        if "PC MASTRCRD" in desc:
            return (5750, "Mastercard", Decimal("98"))
        if "AMEX CARDS" in desc:
            return (5725, "AMEX", Decimal("98"))
        return None
    
    # ========== RULE 5: Payroll Tax ==========
    def _rule_payroll_tax(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """RECEIVER GENERAL / CRA → 2100 Employee Tax Deductions (LIABILITY), 95% confidence"""
        if "RECEIVER GENERAL" in desc or "CRA" in desc:
            return (2100, "Employee Tax Deductions", Decimal("95"))
        return None
    
    # ========== RULE 6: Payroll/Wages ==========
    def _rule_payroll_wages(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """CONCETTA BOSH / CHRISTINA BOSH → 5850 Wages & Benefits, 85% confidence"""
        if "CONCETTA BOSH" in desc or "CHRISTINA BOSH" in desc:
            return (5850, "Wages & Benefits", Decimal("85"))
        return None
    
    # ========== RULE 7: Car Lease ==========
    def _rule_car_lease(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """SPL LOAN → 5155 Car Lease, 90% confidence"""
        if "SPL LOAN" in desc:
            return (5155, "Car Lease", Decimal("90"))
        return None
    
    # ========== RULE 8: Rent ==========
    def _rule_rent(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """RENT → 5800 Rent, 85% confidence"""
        if "RENT" in desc:
            return (5800, "Rent", Decimal("85"))
        return None
    
    # ========== RULE 9: Catch-all Suspense ==========
    def _rule_suspense_fallback(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """Fallback: no rule matched → 5900 Suspense, 0% confidence (manual review)"""
        return (5900, "Suspense", Decimal("0"))


# Module-level function for easy access
def categorize_concetta(description: str, amount: Decimal) -> Tuple[int, str, Decimal]:
    """
    Quick function to categorize a single transaction using Concetta's ruleset.

    Usage:
        account_num, account_name, confidence = categorize_concetta("ECONOMICAL INS", Decimal("-182.41"))
    """
    ruleset = ConcettaRuleset()
    return ruleset.categorize(description, amount)


# ===========================================================================
# Canadian Federation of theotherapy — church / non-profit
# Derived from the FY2024 General Ledger (Sage 50 bridge read, 1,152 lines).
# GL codes are the client's real Sage 50 display codes.  Suspense = 5800.
# ===========================================================================

class TheotherapyRuleset:
    """Client-specific categorization for Canadian Federation of theotherapy.

    Rules apply in priority order; first match wins.  Fee/specific rules run
    before generic ones (e.g. "E-TRANSFER FEE" must beat the tithes rule that
    keys on "E-TRANSFER").  Account codes and the description keywords are taken
    from how the prior bookkeeper coded FY2024.
    """

    SUSPENSE_GL = 5800

    def __init__(self):
        self.rules = [
            self._rule_bank_fees,        # 5690
            self._rule_credit_card,      # 5645
            self._rule_gas,              # 5730
            self._rule_telecom,          # 5780
            self._rule_internet,         # 5775
            self._rule_insurance_auto,   # 5688  (before generic insurance)
            self._rule_insurance,        # 5685
            self._rule_utilities,        # 5790
            self._rule_rent_julian,      # 5761  (before generic rent)
            self._rule_rent,             # 5760
            self._rule_office,           # 5700
            self._rule_seminar,          # 5630
            self._rule_donation,         # 5680
            self._rule_paypal,           # 5240
            self._rule_payroll_remit,    # 2130  (CANACT)
            self._rule_payroll_people,   # 2160  (recurring named payees)
            self._rule_tithes,           # 4020  (deposits only)
            self._rule_suspense_fallback,# 5800
        ]

    def categorize(self, description: str, amount: Decimal) -> Tuple[int, str, Decimal]:
        desc = description.upper()
        for rule in self.rules:
            result = rule(desc, amount)
            if result is not None:
                return result
        return (self.SUSPENSE_GL, "Suspense", Decimal("0"))

    # -- 5690 Interest & Bank Charges -------------------------------------
    def _rule_bank_fees(self, desc, amount):
        for kw in ("E-TRANSFER FEE", "E-TRASNFER FEE", "WIRE PAYMENT FEE",
                   "TRANSFER FEE", "PURCHASE FEE", "OVERDRAFT", "BANK CHARGE",
                   "SERVICE CHARGE", "MONTHLY PLAN FEE", "MONTHLY FEE",
                   "MAINTENANCE FEE", "HANDLING CHG"):
            if kw in desc:
                return (5690, "Interest & Bank Charges", Decimal("95"))
        return None

    # -- 5645 Credit Card Charges -----------------------------------------
    def _rule_credit_card(self, desc, amount):
        for kw in ("BMO MC", "MC CIBC", "M/C-CIBC", "M/C", "MASTERCARD", "CIBC MC"):
            if kw in desc:
                return (5645, "Credit Card Charges", Decimal("90"))
        return None

    # -- 5730 Motor Vehicle Expenses (fuel) -------------------------------
    def _rule_gas(self, desc, amount):
        # Fuel stations from FY2024 GL (all coded to 5730). Added SHELL/HUSKY/
        # KANATA FUEL/KING GEORGE GAS/COLBORNE STREE + common brands the prior
        # list missed. (MOBIL is intentionally NOT here — FY2024 coded it to 5780.)
        for kw in ("PIONEER", "COSTCO GAS", "COTSCO GAS", "CANADIAN TIRE GAS",
                   "ESSO", "PETRO", "COLBORNE GAS", "COLBORNE STREE", "ECHO PLACE CARW",
                   "CARWASH", "SHELL", "HUSKY", "KANATA FUEL", "KING GEORGE GAS",
                   "ULTRAMAR", "FAS GAS"):
            if kw in desc:
                return (5730, "Motor Vehicle Expenses", Decimal("95"))
        return None

    # -- 5780 Telephone ---------------------------------------------------
    def _rule_telecom(self, desc, amount):
        for kw in ("VIRGIN PLUS", "KOODO", "FREEDOM MOBILE", "ROGERS",
                   "BRANTFORD MOBILE"):
            if kw in desc:
                return (5780, "Telephone", Decimal("95"))
        return None

    # -- 5775 Internet Service --------------------------------------------
    def _rule_internet(self, desc, amount):
        if "BELL" in desc:
            return (5775, "Internet Service", Decimal("90"))
        return None

    # -- 5688 Insurance - Auto --------------------------------------------
    def _rule_insurance_auto(self, desc, amount):
        if "BENEVA" in desc:
            return (5688, "Insurance - Auto", Decimal("90"))
        return None

    # -- 5685 Insurance ---------------------------------------------------
    def _rule_insurance(self, desc, amount):
        for kw in ("DAG INS", "CERTAS", "NORTHBRIDGE", "DESJ.SEC.FIN", "DESJ",
                   " INS"):
            if kw in desc:
                return (5685, "Insurance", Decimal("90"))
        return None

    # -- 5790 Utilities ---------------------------------------------------
    def _rule_utilities(self, desc, amount):
        for kw in ("GRANDBRIDGE", "GRAND ENERGY", "HYDRO QUEBEC", "HYDRO",
                   "CLEAN CUT", "ENBRIDGE", "ENERGY", "ENRG"):
            if kw in desc:
                return (5790, "Utilities", Decimal("95"))
        return None

    # -- 5761 Rent - Julian (before generic rent) -------------------------
    def _rule_rent_julian(self, desc, amount):
        if "JULIAN ANTHONY" in desc or "RENT - JULIAN" in desc:
            return (5761, "Rent - Julian", Decimal("90"))
        return None

    # -- 5760 Rent --------------------------------------------------------
    def _rule_rent(self, desc, amount):
        if "RENT" in desc:
            return (5760, "Rent", Decimal("90"))
        return None

    # -- 5700 Office Supplies ---------------------------------------------
    def _rule_office(self, desc, amount):
        for kw in ("STAPLES", "REXALL", "DOLLARAMA", "DOLLORAMA"):
            if kw in desc:
                return (5700, "Office Supplies", Decimal("85"))
        return None

    # -- 5630 Seminar & Conferences ---------------------------------------
    def _rule_seminar(self, desc, amount):
        for kw in ("MEETING", "SEMINAR", "CONFERENCE"):
            if kw in desc:
                return (5630, "Seminar & Conferences", Decimal("85"))
        return None

    # -- 5680 Donation to need peoples ------------------------------------
    def _rule_donation(self, desc, amount):
        if "DONATION" in desc or "195 HENRY" in desc:
            return (5680, "Donation to need peoples", Decimal("85"))
        return None

    # -- 5240 PayPal (matches prior-year coding; low confidence -> review) -
    def _rule_paypal(self, desc, amount):
        if "PAYPAL" in desc:
            return (5240, "Early Payment Purchase Discounts", Decimal("70"))
        return None

    # -- 2130 Employee tax deductions (payroll remittance) ----------------
    def _rule_payroll_remit(self, desc, amount):
        if "CANACT" in desc:
            return (2130, "Employee tax deductions", Decimal("85"))
        return None

    # -- 2160 Payroll Clearing (recurring named payees) -------------------
    #    Lower confidence: suggested account, still routed to human review.
    def _rule_payroll_people(self, desc, amount):
        for kw in ("MAURICIO EMILIAN", "CHRISTIANO OROZCO", "MARTHA EMILIANI",
                   "EDGAR DURAN", "ANILSA MANRIQUE"):
            if kw in desc:
                return (2160, "Payroll Clearing", Decimal("70"))
        return None

    # -- 4020 Tithes (incoming deposits only) -----------------------------
    def _rule_tithes(self, desc, amount):
        if amount > 0 and ("E-TRANSFER" in desc or "INTERAC" in desc
                           or "DEPOSIT" in desc):
            return (4020, "Tithes", Decimal("85"))
        return None

    # -- 5800 Suspense (fallback) -----------------------------------------
    def _rule_suspense_fallback(self, desc, amount):
        return (self.SUSPENSE_GL, "Suspense", Decimal("0"))


# ===========================================================================
# R.L. Electric Inc. — electrical contractor, Ontario
# Derived from FY2021 full-year General Ledger (2026-06-15).
# Keywords taken directly from how the prior bookkeeper coded FY2021.
# Key non-obvious choices vs. default rules:
#   - Gas stations (ESSO, SHELL, PETRO) → 5700 Travel/Meals, NOT 5150 Auto
#     (accountant batched fuel with meals — follows actual GL coding)
#   - Personal items (NETFLIX, AMAZON, PLANET FITNESS, ABM W\D, UBER, E-TFR)
#     → 2750 Shareholder's Advance with confidence 0 (always needs review)
#   - TD LOAN → 2625 Bank Loan (recurring monthly payment)
#   - Revenue is "DEPOSITS" / "DEPOSIT" via D1 source, NOT E-TRANSFER
# GL codes are the client's real Sage 50 display codes.  Suspense = 5900.
# ===========================================================================

class RLElectricRuleset:
    """Client-specific categorization for R.L. Electric Inc.

    Rules apply in priority order; first match wins.
    Bank-fee keywords run first. Specific payees (Paul Wolf, Weston Electric)
    run before generic material-supply rules. Shareholder/personal items route
    to 2750 with confidence 0 so they always enter the review queue.
    """

    SUSPENSE_GL = 5900

    def __init__(self):
        self.rules = [
            self._rule_bank_fees,       # 5200 — BMO fee descriptions from FY2021
            self._rule_td_loan,         # 2625 — monthly loan payment
            self._rule_telecom,         # 5600 — Bell / Virgin (FY2021 only carriers)
            self._rule_payroll_remit,   # 2100 — Receiver General / CRA
            self._rule_materials,       # 5450 — named electrical suppliers first
            self._rule_revenue,         # 4050 — DEPOSITS (D1 source)
            self._rule_meals_gas,       # 5700 — fuel+food coded together per FY2021
            self._rule_shareholder,     # 2750 — personal items → review queue
            self._rule_suspense,        # 5900 — fallback
        ]

    def categorize(self, description: str, amount: Decimal) -> Tuple[int, str, Decimal]:
        desc = description.upper()
        for rule in self.rules:
            result = rule(desc, amount)
            if result is not None:
                return result
        return (self.SUSPENSE_GL, "Suspense", Decimal("0"))

    # -- 5200 Bank Charges & Interest -------------------------------------
    # Keywords taken from actual FY2021 GL entries on GL 1100 and 5200.
    # "PERFORMANCE FEE" / "PERFORMANCE PLAN FEE" is BMO's monthly account fee.
    # "NSF" anchored to word start — "NSF" is a substring of "traNSFer".
    def _rule_bank_fees(self, desc, amount):
        for kw in ("PERFORMANCE FEE", "PERFORMANCE PLAN FEE",
                   "PERFORMANCE AND OVERDRAFT FEE",
                   "RETURNED ITEM FEE", "INTEREST PAID",
                   "INTEREST CHARGE", "OVERDRAFT FEE"):
            if kw in desc:
                return (5200, "Bank Charges & Interest", Decimal("95"))
        if " NSF" in desc or desc.startswith("NSF"):
            return (5200, "Bank Charges & Interest", Decimal("95"))
        return None

    # -- 2625 Bank Loan ---------------------------------------------------
    def _rule_td_loan(self, desc, amount):
        if "TD LOAN" in desc:
            return (2625, "Bank Loan", Decimal("95"))
        return None

    # -- 5600 Telephone & Cellular ----------------------------------------
    # FY2021 GL shows only BELL ONE BILL and VIRGIN; keep broad Bell/Virgin
    # match to catch minor description variants.
    def _rule_telecom(self, desc, amount):
        if "BELL" in desc or "VIRGIN" in desc:
            return (5600, "Telephone & Cellular", Decimal("95"))
        return None

    # -- 2100 Employee Tax Deduction (Receiver General remittances) -------
    def _rule_payroll_remit(self, desc, amount):
        if "RECEIVER GENERAL" in desc or "RECEIVEUR GENERAL" in desc:
            return (2100, "Employee Tax Deduction", Decimal("90"))
        if "CRA" in desc and amount < 0:
            return (2100, "Employee Tax Deduction", Decimal("80"))
        return None

    # -- 5450 Materials & Supplies ----------------------------------------
    # Named electrical suppliers appear in FY2021 GL under 5450.
    # Home Depot and Rona also confirmed in FY2021.
    def _rule_materials(self, desc, amount):
        for kw in ("PAUL WOLF ELECTRIC", "PAUL WOLF ELECTR",
                   "WESTON ELECTRIC", "WESTON ELECT",
                   "HUDCO ELECTRIC", "RATEX ELECTRICAL",
                   "WESTBURNE", "SAM SUPPLY", "JENCO",
                   "THE HOME DEPOT", "HOME DEPOT",
                   "RONA"):
            if kw in desc:
                return (5450, "Materials & Supplies", Decimal("95"))
        return None

    # -- 4050 General Revenue ---------------------------------------------
    # FY2021: revenue entries use description "DEPOSITS" / "DEPOSIT" / "DESPOSITS"
    # (bookkeeper typo — DESPOSITS contains no substring "DEPOSIT") with source D1.
    # Match on positive amounts only.
    def _rule_revenue(self, desc, amount):
        if amount > 0 and ("DEPOSIT" in desc or "DESPOSIT" in desc):
            return (4050, "General Revenue", Decimal("90"))
        return None

    # -- 5700 Travel, Meals & Entertainment -------------------------------
    # FY2021 GL codes fuel (ESSO, SHELL, PETRO CANADA) to 5700 alongside food —
    # bookkeeper batched mixed debit-card spending into one line.  Follow that
    # convention so categorization matches prior-year coding.
    def _rule_meals_gas(self, desc, amount):
        for kw in ("TIM HORTON", "MCDONALD", "MCDONALS", "SUBWAY", "PIZZA PIZZA",
                   "BOSTON PIZZA", "LCBO", "THE BEER STORE", "BEER STORE",
                   "NOFRILLS", "NO FRILLS", "METRO ", "CENTRA FOOD",
                   "ESSO", "SHELL", "PETRO CANADA", "PETRO GAS",
                   "PIONEER", "HUSKY", "ULTRAMAR"):
            if kw in desc:
                return (5700, "Travel, Meals & Entertainment", Decimal("85"))
        return None

    # -- 2750 Shareholder's Advance (personal / owner drawings) -----------
    # Confidence 0 → always enters review queue with GL 2750 as suggestion.
    # Reviewer confirms or redirects before posting.
    def _rule_shareholder(self, desc, amount):
        for kw in ("NETFLIX", "AMAZON PRIME", "AMAZON MEMBER",
                   "PLANET FITNESS", "EQUIFAX",
                   "UBER", "E-TFR", "E-TRF"):
            if kw in desc:
                return (2750, "Shareholder's Advance", Decimal("0"))
        # Cash withdrawals (ABM W\D, W\D, ABC W\D) — always review
        if "ABM W" in desc or (("W\\D" in desc or "W/D" in desc) and amount < 0):
            return (2750, "Shareholder's Advance", Decimal("0"))
        return None

    # -- 5900 Suspense (fallback) -----------------------------------------
    def _rule_suspense(self, desc, amount):
        return (self.SUSPENSE_GL, "Suspense", Decimal("0"))


# ---------------------------------------------------------------------------
# Client ruleset registry — BookkeepingAgent selects by client_id substring.
# ---------------------------------------------------------------------------

_CLIENT_RULESETS: dict[str, type] = {
    "concetta":    ConcettaRuleset,
    "theotherapy": TheotherapyRuleset,
    "rlelectric":  RLElectricRuleset,
}


def get_ruleset(client_id: str):
    """Return a ruleset instance for *client_id* (substring match), or None."""
    cid = (client_id or "").lower()
    for key, cls in _CLIENT_RULESETS.items():
        if key in cid:
            return cls()
    return None