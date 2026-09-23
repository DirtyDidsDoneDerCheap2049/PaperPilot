"""Evidence-bound claim coverage shared by every research direction."""


def build_direction_coverage(direction: dict, claims: list[dict], audit: list[dict]):
    """Use only audited claim verdicts with verified full-text source IDs."""
    claim_by_id = {c['claim_id']: c['claim'] for c in claims}
    cells, records = [], []
    for paper in audit:
        if paper.get('assessment_status') != 'audited' or paper.get('audit_mode') == 'deterministic_fallback':
            continue
        if paper.get('source_type') != 'paper_fulltext':
            continue
        allowed = {e['evidence_id'] for e in paper.get('evidence', []) if e.get('evidence_level') == 'direct_fulltext'}
        relations = paper.get('method_relations') or ['other']
        evidence_ids, covered_claims = set(), []
        for assessment in paper.get('claim_assessments', []):
            claim_id = assessment.get('claim_id')
            verdict = assessment.get('verdict')
            ids = sorted(allowed.intersection(assessment.get('evidence_ids') or []))
            if claim_id not in claim_by_id or not ids or verdict not in {'covered', 'partially_covered', 'contradicted'}:
                continue
            level = 'partial' if verdict == 'partially_covered' else 'direct'
            cells.append({
                'coordinates': {'research_claim': claim_id, 'method_relation': list(relations)},
                'paper_ids': [paper['paper_id']], 'coverage': level,
                'inclusion_reasons': [assessment.get('reason') or claim_by_id[claim_id]],
                'evidence_ids': ids, 'claim_verdict': verdict,
            })
            covered_claims.append(claim_id)
            evidence_ids.update(ids)
        if evidence_ids:
            records.append({
                'paper_id': paper['paper_id'], 'source_type': 'paper_fulltext',
                'coverage_eligible': True, 'coverage_basis': 'research_claims',
                'coverage_level': 'partial', 'claim_ids': sorted(set(covered_claims)),
                'method_relations': list(relations), 'evidence_ids': sorted(evidence_ids),
                'method_facts': paper.get('method_facts', {}),
            })
    matrix = {
        'design': 'direction_claim_evidence',
        'scope': {'research_question': direction.get('research_question', ''),
                  'note': '主张与方法关系的证据索引；未归类、未检索和缺证据均不表示研究空白。'},
        'axes': [{'name': 'research_claim', 'values': list(claim_by_id)},
                 {'name': 'method_relation', 'values': sorted({r for p in records for r in p['method_relations']})}],
        'claims': claims, 'cells': cells,
    }
    return matrix, records


def ground_direction_candidates(result: dict, records: list[dict]) -> dict:
    """Retain proposed experiments; never replace them with distillation templates."""
    by_id = {r['paper_id']: r for r in records if r.get('coverage_eligible')}
    accepted, provisional, rejected, seen = [], [], [], set()
    for origin in ('gaps', 'provisional_gaps', 'rejected_gaps'):
        for raw in result.get(origin) or []:
            if not isinstance(raw, dict):
                continue
            gap = dict(raw)
            key = str(gap.get('gap_id') or gap.get('name') or '')
            if key and key in seen:
                continue
            seen.add(key)
            gap.setdefault('gap_id', f'candidate_{len(seen)}')
            nearest = [w for w in gap.get('nearest_works') or [] if isinstance(w, dict)
                       and w.get('paper_id') in by_id and str(w.get('difference') or '').strip()]
            gap['nearest_works'] = nearest
            valid_evidence = []
            for evidence in gap.get('evidence') or []:
                if not isinstance(evidence, dict):
                    continue
                pid = evidence.get('paper_id')
                ids = evidence.get('evidence_ids') or [evidence.get('evidence_id')]
                if not isinstance(ids, list):
                    ids = [ids]
                valid_ids = sorted(set(str(e) for e in ids if e).intersection(by_id.get(pid, {}).get('evidence_ids', [])))
                if valid_ids:
                    valid_evidence.append({'paper_id': pid, 'evidence_ids': valid_ids})
            gap['evidence'] = valid_evidence
            # A model's rejection alone is not verified direct counterevidence.
            gap['contradicted_by'] = []
            if (origin == 'gaps' and gap.get('candidate_status') in {'supported_candidate', 'narrow_candidate'}
                    and nearest and valid_evidence and gap.get('novelty_basis') and gap.get('task_specific_contribution')):
                gap['candidate_status'] = 'narrow_candidate'
                accepted.append(gap)
            else:
                gap['candidate_status'] = 'rejected' if origin == 'rejected_gaps' else 'insufficient_evidence'
                missing=[]
                if not nearest:missing.append('缺少本轮有原文支持的最近工作及具体差异')
                if not valid_evidence:missing.append('候选引用未绑定本轮有效全文证据')
                if not gap.get('novelty_basis'):missing.append('未说明新颖性依据')
                if not gap.get('task_specific_contribution'):missing.append('未说明任务特定贡献')
                gap.setdefault('reliability_notes',[])
                if not isinstance(gap['reliability_notes'],list):gap['reliability_notes']=[gap['reliability_notes']]
                gap['reliability_notes'].extend(missing)
                gap['coverage_risk'] = gap.get('coverage_risk') or '当前仅为待验证假设；没有搜到不能证明空白。'
                (rejected if origin == 'rejected_gaps' else provisional).append(gap)
    result.update(gaps=accepted, provisional_gaps=provisional, rejected_gaps=rejected)
    return result


def enforce_candidate_consistency(result: dict) -> dict:
    """A rejection is not proof of direct counterevidence; keep statuses exclusive."""
    seen = set()
    for bucket in ('gaps', 'rejected_gaps', 'provisional_gaps'):
        items = []
        for item in result.get(bucket) or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get('gap_id') or item.get('name') or '')
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            if bucket != 'gaps':
                item['candidate_status'] = 'rejected' if bucket == 'rejected_gaps' else 'insufficient_evidence'
                item['contradicted_by'] = []
            items.append(item)
        result[bucket] = items
    return result
