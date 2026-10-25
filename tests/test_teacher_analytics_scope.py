"""Exercise the MySQL read-model SQL against isolated relational fixtures."""
import asyncio
from datetime import date
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

from core.identity import AccessDeniedError, AuthenticatedPrincipal
from gateway.mysql_repository import MySQLGatewayRepository
from server.teacher.service import TeacherService


@pytest.fixture
def repository():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    @event.listens_for(engine, 'connect')
    def clock(connection, _record):
        connection.create_function('UTC_TIMESTAMP', 0, lambda: '2026-10-25 00:00:00')
    tables = [
        'nlp_users(id TEXT,username TEXT,display_name TEXT,status TEXT,deleted_at TEXT)',
        'nlp_roles(id TEXT,code TEXT,status TEXT)',
        'nlp_user_roles(user_id TEXT,role_id TEXT,expires_at TEXT)',
        'nlp_classrooms(id TEXT,status TEXT)',
        'nlp_classroom_members(classroom_id TEXT,user_id TEXT,member_role TEXT,status TEXT)',
        'nlp_turns(id TEXT,conversation_id TEXT,user_id TEXT,workspace_id TEXT,error_kind TEXT,created_at TEXT,learning_state_json TEXT)',
        'nlp_exercise_questions(id TEXT,question TEXT,exercise_session_id TEXT)',
        'nlp_exercise_sessions(id TEXT,user_id TEXT,workspace_id TEXT,topic_id TEXT,mode TEXT,completed_at TEXT,blueprint_snapshot_json TEXT)',
        'nlp_exercise_attempts(exercise_question_id TEXT,rubric_matches_json TEXT)',
        'nlp_learning_evidence(exercise_question_id TEXT,exercise_session_id TEXT,normalized_score INTEGER,passed INTEGER,blueprint_snapshot_json TEXT,rubric_matches_json TEXT)',
        'nlp_guided_sessions(id TEXT,user_id TEXT,workspace_id TEXT,topic_id TEXT,state_json TEXT,created_at TEXT,completed_at TEXT)',
    ]
    with engine.begin() as c:
        for table in tables:
            c.execute(text('CREATE TABLE ' + table))
        c.execute(text("INSERT INTO nlp_roles VALUES('student','student','active'),('guest','guest','active')"))
        for user in ('a', 'b', 'shared', 'expired', 'inactive', 'removed', 'guest'):
            c.execute(text('INSERT INTO nlp_users VALUES(:id,:id,:id,:status,NULL)'), {'id': user, 'status': 'inactive' if user == 'inactive' else 'active'})
            c.execute(text('INSERT INTO nlp_user_roles VALUES(:id,:role,:expires)'),
                      {'id': user, 'role': 'guest' if user == 'guest' else 'student', 'expires': '2000-01-01' if user == 'expired' else None})
        c.execute(text("INSERT INTO nlp_classrooms VALUES('ca','active'),('cb','active'),('old','inactive')"))
        assignments = [('ca','ta','teacher','active'),('cb','tb','teacher','active'),('cb','ta','student','active'),
                       ('old','ta','teacher','active'),('ca','a','student','active'),('cb','b','student','active'),
                       ('ca','shared','student','active'),('cb','shared','student','active'),
                       ('ca','expired','student','active'),('ca','inactive','student','active'),
                       ('ca','removed','student','inactive'),('ca','guest','student','active')]
        for classroom,user,role,status in assignments:
            c.execute(text('INSERT INTO nlp_classroom_members VALUES(:c,:u,:r,:s)'), dict(c=classroom,u=user,r=role,s=status))
        for user in ('a','b','shared','guest'):
            c.execute(text('INSERT INTO nlp_turns VALUES(:id,:id,:u,:w,NULL,:created,:state)'),
                      dict(id='turn-'+user,u=user,w='personal-'+user,created='2026-10-22 09:00:00',state='{}'))
        for user,day in [('a','2026-10-21 00:00:00'),('b','2026-10-22 00:00:00')]:
            c.execute(text('INSERT INTO nlp_exercise_questions VALUES(:id,:question,:id)'),dict(id=user,question='exercise-'+user))
            c.execute(text("INSERT INTO nlp_exercise_sessions VALUES(:id,:id,:w,'topic','practice',:day,:blueprint)"),dict(id=user,w='personal-'+user,day=day,blueprint='{}'))
            c.execute(text("INSERT INTO nlp_exercise_attempts VALUES(:id,'[]')"),dict(id=user))
            c.execute(text('INSERT INTO nlp_learning_evidence VALUES(:id,:id,90,1,:blueprint,:rubric)'),
                      dict(id=user,blueprint=json.dumps({'knowledge_point_ids':['point']}),rubric='[]'))
            c.execute(text("INSERT INTO nlp_guided_sessions VALUES(:id,:id,:w,'topic','{}',:day,:day)"),dict(id=user,w='personal-'+user,day=day))
    repo = object.__new__(MySQLGatewayRepository)
    repo._engine = engine
    repo.get_teaching_catalog = lambda _workspace: {'catalog': {'topics': []}}
    yield repo
    engine.dispose()


def test_teacher_roster_excludes_other_classes_and_inactive_grants(repository):
    scope = repository.teacher_analytics_scope('ta')
    assert scope == {'student_user_ids': {'a','shared'}, 'classroom_count': 1}
    assert repository.teacher_analytics_scope('tb')['student_user_ids'] == {'b','shared'}
    assert repository.teacher_analytics_scope('unknown') == {'student_user_ids': set(), 'classroom_count': 0}


def test_scoped_questions_include_personal_workspaces_without_other_students(repository):
    rows = repository.list_question_turns(workspace_id=None, analytics_student_ids={'a','shared'}, since='2026-10-01')
    assert {row['user_id'] for row in rows} == {'a','shared'}
    assert not repository.list_question_turns(workspace_id='teacher-personal', since='2026-10-01')


@pytest.mark.parametrize('method', ['list_question_turns','exercise_evidence_stats','exercise_criterion_stats','guided_session_stats'])
def test_empty_scope_never_falls_back_to_all_students(repository, method):
    result = getattr(repository, method)(workspace_id=None, analytics_student_ids=set(), since='2026-10-01')
    assert result == ([],False) if method.startswith('exercise_') else result == []


@pytest.mark.parametrize('method', ['exercise_evidence_stats','exercise_criterion_stats','guided_session_stats'])
def test_student_filter_applies_before_the_read_limit(repository, method):
    result = getattr(repository, method)(workspace_id=None, analytics_student_ids={'a'}, since='2026-10-01', limit=1)
    rows = result[0] if method.startswith('exercise_') else result
    assert len(rows) == 1 and rows[0]['user_id'] == 'a'
    if method.startswith('exercise_'):
        assert result[1] is False


def test_unscoped_reads_are_rejected(repository):
    with pytest.raises(ValueError, match='explicit workspace or student scope'):
        repository.list_question_turns(workspace_id=None, since='2026-10-01')


@pytest.mark.parametrize(('role','user','expected','mode'), [
    ('teacher','ta',2,'classroom_students'),('teacher','unknown',0,'classroom_students'),
    ('developer','dev',3,'all_students'),
])
def test_service_selects_scope_on_the_server(repository, role, user, expected, mode):
    principal = AuthenticatedPrincipal(user_id=user,roles=frozenset({role}),workspace_ids=frozenset({'teacher-personal'}))
    result = asyncio.run(TeacherService().analytics(principal, SimpleNamespace(repository=repository), 'teacher-personal',
        period_start=date(2026,10,1), period_end=date(2026,10,25)))
    assert result['summary']['questions'] == expected
    assert result['data_scope']['mode'] == mode
    if user == 'unknown':
        assert result['data_scope']['classroom_count'] == 0
        assert '任教班级' in result['data_scope']['message']


def test_guest_cannot_invoke_teacher_statistics(repository):
    principal = AuthenticatedPrincipal(user_id='guest',roles=frozenset({'guest'}),workspace_ids=frozenset({'teacher-personal'}))
    with pytest.raises(AccessDeniedError):
        asyncio.run(TeacherService().analytics(principal, SimpleNamespace(repository=repository), 'teacher-personal'))
