import json
from collections import Counter
from clindoc.audit.benchmark_auditor import BenchmarkAuditor, SemanticCategory

auditor = BenchmarkAuditor()
supported_wrong_entities = []

with open('data/m2_verification/all_examples.jsonl', 'r', encoding='utf-8') as f:
    for line in f:
        d = json.loads(line)
        if d['label'] == 'SUPPORTED':
            from clindoc.m2.schema import M2Example
            ex = M2Example.model_validate(d)
            rec = auditor.audit_example(ex)
            if rec.semantic_category == SemanticCategory.WRONG_ENTITY:
                supported_wrong_entities.append(d)

print(f"Total SUPPORTED with WRONG_ENTITY: {len(supported_wrong_entities)}")
sev_counts = Counter(d['severity'] for d in supported_wrong_entities)
gen_counts = Counter(d['generation_method'] for d in supported_wrong_entities)
field_counts = Counter(d['field_type'] for d in supported_wrong_entities)

print("Severity counts:", dict(sev_counts))
print("Generation methods:", dict(gen_counts))
print("Field counts:", dict(field_counts))

# Inspect 3 samples
print("\nSample 1:")
s1 = supported_wrong_entities[0]
print(f"  Field: {s1['field_type']}, Claim: {s1['claim']}, Doc: {s1['source_doc_id']}, Sev: {s1['severity']}, Gen: {s1['generation_method']}")
print(f"  Evidence snippet: {repr(s1['evidence'][:120])}")

print("\nSample 2:")
s2 = supported_wrong_entities[len(supported_wrong_entities)//2]
print(f"  Field: {s2['field_type']}, Claim: {s2['claim']}, Doc: {s2['source_doc_id']}, Sev: {s2['severity']}, Gen: {s2['generation_method']}")
print(f"  Evidence snippet: {repr(s2['evidence'][:120])}")
