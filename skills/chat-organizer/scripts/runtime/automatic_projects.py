"""Resolve approved new projects, create their folders, then apply grouping."""
import hashlib
from pathlib import Path
from batch_cache import ROOT, encoded
from offline_registration import load, save
from project_admin import ensure_project, project_list
from desktop_mcp_probe import DesktopMcp
from project_executor import call
from probe import Rpc
from review_workflow import session_path, validate_approval, apply_groups


def apply(sid, approval_path):
    session = load(session_path(sid))
    approval = load(approval_path)
    validate_approval(session, approval)  # Before folders, connections or creation.
    names = sorted({d['destination'][4:] for d in approval['decisions']
                    if isinstance(d['destination'], str) and d['destination'].startswith('new:')})
    if not names:
        return apply_groups(sid, approval_path)
    if any(not name.strip() or name != name.strip() or len(name) > 100 for name in names):
        raise ValueError('Approved new project names must be nonempty, trimmed and at most 100 characters')
    directory = ROOT / 'work' / 'automatic-projects' / sid
    directory.mkdir(parents=True, exist_ok=True)
    explicit = approval.get('project_roots', {})
    if not isinstance(explicit, dict) or set(explicit) - set(names):
        raise ValueError('project_roots must refer only to approved new projects')
    roots_path = directory / 'roots.json'
    roots = load(roots_path) if roots_path.exists() else {}
    rpc = Rpc()
    try:
        catalog = project_list(rpc)
        # Resolve every folder before any create. Persist choices across retries.
        for name in names:
            matches = [p for p in catalog if p['name'] == name]
            if len(matches) > 1:
                raise ValueError('Multiple same-name projects: choose an existing ID instead of creating another')
            if name in roots:
                if name in explicit and explicit[name] != roots[name]:
                    raise ValueError('Do not change a saved project root intent')
                continue
            if name in explicit:
                from settings import absolute
                if not isinstance(explicit[name], list) or not explicit[name]:
                    raise ValueError('project_roots values must be nonempty absolute-path lists')
                roots[name] = [str(absolute(p)) for p in explicit[name]]
                if matches and roots[name] != [r['path'] for r in matches[0]['roots']]:
                    raise ValueError('Same-name project has different approved roots')
            elif matches:
                roots[name] = [r['path'] for r in matches[0]['roots']]
            else:
                # Hash keeps arbitrary project names out of filesystem paths.
                key = hashlib.sha256(name.encode()).hexdigest()[:20]
                roots[name] = [str((ROOT / 'projects' / ('project-' + key)).resolve())]
        save(roots_path, roots)
        resolved = {}
        for name in names:
            for folder in roots[name]:
                Path(folder).mkdir(parents=True, exist_ok=True)
            key = hashlib.sha256(encoded([sid, name, roots[name]]).encode()).hexdigest()
            project = ensure_project(rpc, name, roots[name], directory / (key + '.json'), key)
            resolved[name] = project['id']
    finally:
        rpc.close()
    client = DesktopMcp()
    try:
        native = {p['projectId'] for p in call(client, 'list_projects', {})['projects']
                  if p.get('projectKind') == 'local'}
    finally:
        client.close()
    missing = {name: pid for name, pid in resolved.items() if pid not in native}
    session['project_creation'] = {'state': 'desktop_registration_required' if missing else 'ready',
        'projects': resolved, 'missing_from_desktop': missing, 'roots_file': str(roots_path),
        'approval_file': str(Path(approval_path).resolve()), 'model_calls': 0,
        'next_action': 'Use existing project IDs and projects-and-recovery.md; never ask user to create duplicates' if missing else 'apply grouping'}
    save(session_path(sid), session)
    if missing:
        return session
    approved = dict(approval, decisions=[{**d, 'destination': resolved[d['destination'][4:]]
                       if isinstance(d['destination'], str) and d['destination'].startswith('new:') else d['destination']}
                       for d in approval['decisions']])
    resolved_path = directory / 'resolved-approval.json'
    save(resolved_path, approved)
    return apply_groups(sid, resolved_path)
