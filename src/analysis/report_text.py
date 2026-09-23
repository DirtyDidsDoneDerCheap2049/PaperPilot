"""Render heterogeneous model explanations without Python object reprs."""
LABELS={'scope':'核查范围','coverage_interpretation':'如何理解覆盖结果',
        'distillation_vs_other':'方法区别','key_findings':'已有实现',
        'remaining_gaps':'剩余问题','limitations':'限制','summary':'总结',
        'paper_id':'论文','evidence_ids':'证据','contradicts':'反驳的主张',
        'explanation':'说明','reason':'理由','required_evidence':'待补证据'}

def readable_text(value):
    if value is None:return ''
    if isinstance(value,dict):
        return '\n\n'.join(f'{LABELS.get(str(k),str(k))}：' + ('\n\n' if isinstance(v,(dict,list)) else '') + readable_text(v) for k,v in value.items() if v not in (None,'',[],{}))
    if isinstance(value,list):
        return '\n'.join('- '+readable_text(v) for v in value)
    return str(value)
