"""Read one saved research snapshot without opening its database or credentials."""
import argparse
import json
from collections import Counter
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--summary',action='store_true')
    args = parser.parse_args()
    data = json.loads(args.snapshot.read_text(encoding='utf-8'))
    direction=data.get('direction') or {}
    if not args.summary:print('direction:',json.dumps(direction,ensure_ascii=False))
    papers = data.get('papers', [])
    print('paper_years:', dict(Counter(str(p.get('year')) for p in papers)))
    if args.summary:
        plan=direction.get('research_plan') or {}
        print('plan:',json.dumps({k:plan.get(k) for k in ('task_type','objective','time_scope')},ensure_ascii=False))
        for item in data.get('decisions',[]):
            print('action:',item.get('step'),item.get('action'),item.get('blocked') or item.get('progress_summary'))
        for profile in data.get('innovations',[]):
            print('profile:',profile.get('paper_id'),'error:',profile.get('error'),'rows:',len(profile.get('benchmark_results',[])))
        print('analysis:',json.dumps(data.get('analysis',{}),ensure_ascii=False)[:9000])
        return
    for paper in papers:
        print(paper.get('id'), paper.get('year'), paper.get('publication_date'), paper.get('title'))


if __name__ == '__main__':
    main()
