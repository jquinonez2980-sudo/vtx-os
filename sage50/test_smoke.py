from sage50.categorization_rules import ConcettaRuleset
from decimal import Decimal
import csv

ruleset = ConcettaRuleset()
auto_categorized = 0
manual_review = 0

print('CONCETTA SMOKE TEST - Real December 2025 Bank Data')
print('=' * 100)

try:
    with open('data/test-client/dec-2025-bank-extracted.csv', 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            desc = row.get('description', '').upper()
            amount = Decimal(row.get('amount', '0'))
            acct, name, conf = ruleset.categorize(desc, amount)
            
            if conf > Decimal('0'):
                auto_categorized += 1
                status = 'AUTO'
            else:
                manual_review += 1
                status = 'MANUAL'
            
            print(f'{status:6} | {desc:30} | {acct:4} {name:30} | {conf}%')
    
    print('=' * 100)
    print(f'RESULTS: {auto_categorized} auto-categorized, {manual_review} manual review')
    total = auto_categorized + manual_review
    if total > 0:
        pct = (auto_categorized / total) * 100
        print(f'Auto-categorization rate: {pct:.1f}%')
except FileNotFoundError as e:
    print(f'ERROR: {e}')