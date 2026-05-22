"""
Rule-based transaction categorizer for Canadian business bank statements.

Each rule maps a regex pattern against the cleaned transaction description
to a GL account code and category label. Rules are evaluated by priority
(lower number = first). First match wins.

GL account structure used (standard Canadian SMB chart of accounts):
  1060  Bank – Chequing
  1200  Accounts Receivable
  2100  Payroll Liabilities (CPP/EI/Tax Withheld)
  2200  HST/GST Payable
  2210  HST/GST Recoverable (Input Tax Credits)
  4100  Consulting / Service Revenue
  5100  Salaries & Wages
  5110  CPP – Employer Contribution
  5120  EI – Employer Contribution
  5200  Professional Fees
  5300  Insurance
  5310  Business Licences & Permits
  5320  Bank Charges & Interest
  5400  Office Supplies
  5410  Computer & IT Equipment
  5420  Postage & Courier
  5500  Utilities
  5600  Rent & Occupancy
  5700  Advertising & Marketing
  5800  Software & Subscriptions
  5900  Travel & Meals
  9999  Unclassified – Needs Review
"""

from __future__ import annotations

import re
from typing import Sequence

from models.banking import BankTransaction, CategorizedTransaction, CategorizationRule


# ---------------------------------------------------------------------------
# Default rules — Canadian business context
# ---------------------------------------------------------------------------

DEFAULT_RULES: list[CategorizationRule] = [
    # Payroll processors
    CategorizationRule(rule_id="payroll-adp",      pattern=r"\bADP\b",
                       gl_account_no="5100", gl_account_name="Salaries & Wages",
                       category="Payroll", priority=1),
    CategorizationRule(rule_id="payroll-ceridian", pattern=r"\bCERIDIAN\b",
                       gl_account_no="5100", gl_account_name="Salaries & Wages",
                       category="Payroll", priority=1),
    CategorizationRule(rule_id="payroll-payworks", pattern=r"\bPAYWORKS\b",
                       gl_account_no="5100", gl_account_name="Salaries & Wages",
                       category="Payroll", priority=1),
    CategorizationRule(rule_id="payroll-generic",  pattern=r"\bPAYROLL\b",
                       gl_account_no="5100", gl_account_name="Salaries & Wages",
                       category="Payroll", priority=2),

    # CRA remittances
    CategorizationRule(rule_id="cra-payroll",
                       pattern=r"(RECEIVER\s+GENERAL|CANADA\s+REVENUE|CRA).*(PAYROLL|DEDUCTION|CPP|EI\b)",
                       gl_account_no="2100", gl_account_name="Payroll Liabilities",
                       category="CRA – Payroll Remittance", priority=1),
    CategorizationRule(rule_id="cra-hst",
                       pattern=r"(RECEIVER\s+GENERAL|CRA|CANADA\s+REVENUE).*(HST|GST|TAX\s+REMIT)",
                       gl_account_no="2200", gl_account_name="HST/GST Payable",
                       category="CRA – HST/GST Remittance", priority=1),
    CategorizationRule(rule_id="cra-income-tax",
                       pattern=r"(RECEIVER\s+GENERAL|CRA|CANADA\s+REVENUE)",
                       gl_account_no="2100", gl_account_name="Payroll Liabilities",
                       category="CRA – Remittance", priority=3),

    # Bank charges
    CategorizationRule(rule_id="bank-service-charge",
                       pattern=r"(SERVICE\s+CHARGE|MONTHLY\s+FEE|ACCOUNT\s+FEE|BANK\s+FEE|NSF\s+FEE|OVERDRAFT)",
                       gl_account_no="5320", gl_account_name="Bank Charges & Interest",
                       category="Bank Charges", priority=2),
    CategorizationRule(rule_id="bank-interest",
                       pattern=r"\bINTEREST\s+(CHARGED|PAYMENT|EXPENSE)\b",
                       gl_account_no="5320", gl_account_name="Bank Charges & Interest",
                       category="Bank Interest", priority=2),

    # Utilities
    CategorizationRule(rule_id="util-hydro",
                       pattern=r"\b(HYDRO|HYDRO\s+ONE|BC\s+HYDRO|MANITOBA\s+HYDRO|NOVA\s+SCOTIA\s+POWER|NB\s+POWER)\b",
                       gl_account_no="5500", gl_account_name="Utilities",
                       category="Hydro / Electricity", priority=2),
    CategorizationRule(rule_id="util-gas",
                       pattern=r"\b(ENBRIDGE|UNION\s+GAS|FORTIS\s+BC|ATCO|HERITAGE\s+GAS)\b",
                       gl_account_no="5500", gl_account_name="Utilities",
                       category="Natural Gas", priority=2),
    CategorizationRule(rule_id="util-water",
                       pattern=r"\b(WATER\s+UTILITY|CITY\s+WATER)\b",
                       gl_account_no="5500", gl_account_name="Utilities",
                       category="Water", priority=2),
    CategorizationRule(rule_id="util-internet",
                       pattern=r"\b(BELL|ROGERS|TELUS|SHAW|VIDEOTRON|COGECO|EASTLINK|INTERNET|TELECOM)\b",
                       gl_account_no="5500", gl_account_name="Utilities",
                       category="Telecom / Internet", priority=3),

    # Rent
    CategorizationRule(rule_id="rent",
                       pattern=r"\b(RENT|LEASE\s+PAYMENT|PROPERTY\s+MGMT|REAL\s+ESTATE)\b",
                       gl_account_no="5600", gl_account_name="Rent & Occupancy",
                       category="Rent", priority=2),

    # Insurance
    CategorizationRule(rule_id="insurance",
                       pattern=r"\b(INSURANCE|INTACT|AVIVA|WAWANESA|CO.OPERATORS|ECONOMICAL|DESJARDINS\s+INS|INTACT)\b",
                       gl_account_no="5300", gl_account_name="Insurance",
                       category="Insurance", priority=2),

    # Professional fees
    CategorizationRule(rule_id="legal",
                       pattern=r"\b(LAW\s+FIRM|LAWYER|LEGAL|NOTARY|BARRISTER)\b",
                       gl_account_no="5200", gl_account_name="Professional Fees",
                       category="Legal Fees", priority=2),
    CategorizationRule(rule_id="accounting",
                       pattern=r"\b(ACCOUNTANT|CPA|BOOKKEEPING|AUDIT)\b",
                       gl_account_no="5200", gl_account_name="Professional Fees",
                       category="Accounting Fees", priority=2),

    # Office supplies
    CategorizationRule(rule_id="office-supplies",
                       pattern=r"\b(STAPLES|BUREAU\s+EN\s+GROS|OFFICE\s+DEPOT|GRAND\s+&\s+TOY|W\.W\.\s+GRAINGER)\b",
                       gl_account_no="5400", gl_account_name="Office Supplies",
                       category="Office Supplies", priority=2),

    # Courier / postage
    CategorizationRule(rule_id="courier",
                       pattern=r"\b(CANADA\s+POST|PUROLATOR|FEDEX|FEDEX|UPS\b|DHL|CANPAR)\b",
                       gl_account_no="5420", gl_account_name="Postage & Courier",
                       category="Postage & Courier", priority=2),

    # Software & subscriptions
    CategorizationRule(rule_id="saas-microsoft",
                       pattern=r"\b(MICROSOFT|OFFICE\s+365|MS\s+365)\b",
                       gl_account_no="5800", gl_account_name="Software & Subscriptions",
                       category="Software – Microsoft", priority=2),
    CategorizationRule(rule_id="saas-google",
                       pattern=r"\b(GOOGLE\s+(WORKSPACE|CLOUD|ADS)|GSUITE)\b",
                       gl_account_no="5800", gl_account_name="Software & Subscriptions",
                       category="Software – Google", priority=2),
    CategorizationRule(rule_id="saas-adobe",
                       pattern=r"\bADOBE\b",
                       gl_account_no="5800", gl_account_name="Software & Subscriptions",
                       category="Software – Adobe", priority=2),
    CategorizationRule(rule_id="saas-generic",
                       pattern=r"\b(SUBSCRIPTION|SOFTWARE|SAAS|APP\s+STORE|PLAY\s+STORE)\b",
                       gl_account_no="5800", gl_account_name="Software & Subscriptions",
                       category="Software & Subscriptions", priority=4),

    # Advertising
    CategorizationRule(rule_id="advertising",
                       pattern=r"\b(FACEBOOK|META\s+ADS|GOOGLE\s+ADS|LINKEDIN|TWITTER|INSTAGRAM|ADVERTISING|MARKETING)\b",
                       gl_account_no="5700", gl_account_name="Advertising & Marketing",
                       category="Advertising", priority=2),

    # Travel & meals
    CategorizationRule(rule_id="travel",
                       pattern=r"\b(AIR\s+CANADA|WESTJET|PORTER|VIA\s+RAIL|HOTEL|MARRIOTT|HILTON|AIRBNB|UBER|LYFT|TAXI)\b",
                       gl_account_no="5900", gl_account_name="Travel & Meals",
                       category="Travel", priority=2),
    CategorizationRule(rule_id="meals",
                       pattern=r"\b(RESTAURANT|TIM\s+HORTONS|STARBUCKS|MCDONALDS|SUBWAY|DOORDASH|UBER\s+EATS|SKIP\s+THE\s+DISHES)\b",
                       gl_account_no="5900", gl_account_name="Travel & Meals",
                       category="Meals", priority=3),

    # Business licence
    CategorizationRule(rule_id="licence",
                       pattern=r"\b(BUSINESS\s+LICEN(C|S)E|CITY\s+OF\s+\w+\s+LICEN|MUNICIPAL\s+LICEN)\b",
                       gl_account_no="5310", gl_account_name="Business Licences & Permits",
                       category="Business Licence", priority=2),

    # Transfers — flag for review (could be inter-account, owner draw, etc.)
    CategorizationRule(rule_id="transfer",
                       pattern=r"\b(TRANSFER|TFR\b|E-TRANSFER|WIRE|WIRE\s+TRANSFER|INTERAC)\b",
                       gl_account_no="9999", gl_account_name="Unclassified – Needs Review",
                       category="Transfer – Review Required", priority=5),
]

# Sort by priority once at module load
DEFAULT_RULES.sort(key=lambda r: r.priority)


# ---------------------------------------------------------------------------
# Categorization engine
# ---------------------------------------------------------------------------

_CONFIDENCE_EXACT    = 0.95   # clean regex match
_CONFIDENCE_TRANSFER = 0.60   # transfers always need review
_THRESHOLD_AUTO      = 0.80   # default auto-approve threshold


def categorize(
    txn: BankTransaction,
    rules: Sequence[CategorizationRule] | None = None,
    threshold: float = _THRESHOLD_AUTO,
) -> CategorizedTransaction:
    rules = rules or DEFAULT_RULES
    desc_upper = txn.description.upper()

    for rule in rules:
        try:
            matched = bool(re.search(rule.pattern, desc_upper, re.IGNORECASE))
        except re.error:
            continue

        if matched:
            confidence = _CONFIDENCE_TRANSFER if rule.rule_id == "transfer" else _CONFIDENCE_EXACT
            return CategorizedTransaction(
                **txn.model_dump(),
                gl_account_no=rule.gl_account_no,
                gl_account_name=rule.gl_account_name,
                category=rule.category,
                confidence=confidence,
                matched_rule_id=rule.rule_id,
                needs_review=confidence < threshold,
            )

    # No rule matched
    return CategorizedTransaction(
        **txn.model_dump(),
        gl_account_no="9999",
        gl_account_name="Unclassified – Needs Review",
        category="Unclassified",
        confidence=0.0,
        matched_rule_id=None,
        needs_review=True,
    )


def categorize_batch(
    transactions: list[BankTransaction],
    rules: Sequence[CategorizationRule] | None = None,
    threshold: float = _THRESHOLD_AUTO,
) -> list[CategorizedTransaction]:
    return [categorize(t, rules, threshold) for t in transactions]
