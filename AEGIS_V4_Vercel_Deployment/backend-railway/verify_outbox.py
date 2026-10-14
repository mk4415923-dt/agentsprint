"""Offline output validator for the competition's required outbox.json format.
Run after python agent.py. Does not send email or call Gemini.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from agent import BASE, FIELDS, extract_policy_codes, load_emails


def verify(outbox: Path, inbox: Path, policy: Path, *, subset=False):
    if not outbox.is_file():
        raise FileNotFoundError('No outbox.json. First run: python agent.py')
    rows = json.loads(outbox.read_text(encoding='utf-8-sig'))
    if not isinstance(rows, list):
        raise ValueError('The outbox must be a list of objects')
    source_ids = {email['email_id'] for email in load_emails(inbox)}
    ids = []
    valid_codes = extract_policy_codes(policy.read_text(encoding='utf-8-sig'))
    errors = []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append(f'Row {n}: not a JSON object')
            continue
        eid = row.get('email_id', f'row {n}')
        ids.append(eid)
        if set(row) != set(FIELDS):
            errors.append(f'{eid}: incorrect fields')
        if row.get('priority') not in ('low', 'medium', 'high'):
            errors.append(f'{eid}: priority invalid')
        if type(row.get('escalate')) is not bool:
            errors.append(f'{eid}: escalate must be true/false')
        if not isinstance(row.get('policy_refs'), list):
            errors.append(f'{eid}: policy_refs must be a list')
        elif any(code not in valid_codes for code in row['policy_refs']):
            errors.append(f'{eid}: includes non-policy code')
        draft = row.get('reply_draft')
        if not isinstance(draft, str):
            errors.append(f'{eid}: reply_draft must be text')
        else:
            if len(draft.split()) > 150:
                errors.append(f'{eid}: exceeds 150 words')
            if 'Student Helpdesk' not in draft:
                errors.append(f'{eid}: missing signature')
        c = row.get('confidence')
        if not isinstance(c, (float, int)) or isinstance(c, bool) or not 0 <= c <= 1:
            errors.append(f'{eid}: confidence outside 0..1')
        if row.get('escalate') is False and row.get('escalation_reason'):
            errors.append(f'{eid}: non-escalated case includes escalation reason')
    if len(ids) != len(set(ids)):
        errors.append('Duplicate output email IDs')
    if not set(ids) <= source_ids:
        errors.append('Output IDs not present in input inbox')
    if not subset and set(ids) != source_ids:
        errors.append(f'Email count mismatch: {len(ids)} output vs {len(source_ids)} inbox')
    print(f'Checked {len(rows)} outputs against {len(source_ids)} inbox messages and {len(valid_codes)} policy rules.')
    print('Escalations:', sum(row.get('escalate') is True for row in rows if isinstance(row, dict)))
    if errors:
        print('VALIDATION FAILED:')
        for msg in errors:
            print(' -', msg)
    else:
        print('PASS: required schema, policy refs, reply length and counts validated.')
    return errors


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--outbox', type=Path, default=BASE/'outbox.json')
    parser.add_argument('--subset', action='store_true', help='Allow outputs from python agent.py --only ID')
    args = parser.parse_args()
    try:
        sys.exit(1 if verify(args.outbox, BASE/'data/inbox', BASE/'data/policy.md', subset=args.subset) else 0)
    except Exception as exc:
        print('ERROR:', exc, file=sys.stderr)
        sys.exit(1)
