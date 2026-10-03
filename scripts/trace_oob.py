import json
from pathlib import Path
from clindoc.audit.benchmark_auditor import BenchmarkAuditor

auditor = BenchmarkAuditor()
eids = [
    '66f8b880-14e7-4bdc-bd96-bd5ae6fa8b35',
    '3831d8a4-24d9-4c13-ab82-8184c605c3d8',
    '38f93953-f159-4eb3-bf90-1fac50b8d923',
    '12e612de-3e4a-48c7-ba20-1295303d18ea',
]

with open('data/m2_verification/all_examples.jsonl', 'r', encoding='utf-8') as f:
    for line in f:
        d = json.loads(line)
        if d['example_id'] in eids:
            doc_id = d['source_doc_id']
            clean_pt = auditor.get_clean_page_text(doc_id, 1)
            deg_pt = auditor.get_degraded_page_text(doc_id, d['severity'])
            print(f"ID: {d['example_id']}")
            print(f"  Doc: {doc_id}, Sev: {d['severity']}, Gen: {d['generation_method']}")
            print(f"  Claim: {d['claim']}, GT: {d['label']}")
            print(f"  Offsets: [{d['char_start']}, {d['char_end']}]")
            print(f"  Clean doc len: {len(clean_pt) if clean_pt else None}")
            print(f"  Degraded doc len: {len(deg_pt) if deg_pt else None}")
            if deg_pt and d['char_end'] <= len(deg_pt):
                print(f"  Degraded slice: repr({deg_pt[d['char_start']:d['char_end']]})")
