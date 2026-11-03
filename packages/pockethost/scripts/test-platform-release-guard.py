#!/usr/bin/env python3
import pathlib,signal,sqlite3,tempfile,unittest,concurrent.futures
from unittest.mock import patch
import platform_release_guard as m


class Guard(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=pathlib.Path(self.temp.name);self.database=self.root/'control.db'
        marker=patch.object(m,'PROJECT_RECOVERY_STATUS',self.root/'project-status.json');marker.start();self.addCleanup(marker.stop)
        barrier=patch.object(m,'database_write_barrier');barrier.start();self.addCleanup(barrier.stop)
        with sqlite3.connect(self.database)as db:
            for table in ['web_deployments','web_storage_jobs','web_task_runs','web_app_backups']:
                db.execute('CREATE TABLE '+table+'(status TEXT)')
            db.execute('CREATE TABLE instance_backups(status TEXT, restoreState TEXT)')
            db.execute('CREATE TABLE web_notification_deliveries(status TEXT, leaseAt TEXT)')

    def test_queued_pushes_do_not_block_quiescence(self):
        with sqlite3.connect(self.database)as db:db.execute("INSERT INTO web_deployments VALUES('queued')")
        self.assertEqual(m.busy(self.database,self.root),{})

    def test_running_work_and_finalization_are_not_interrupted(self):
        with sqlite3.connect(self.database)as db:db.execute("INSERT INTO web_task_runs VALUES('running')")
        (self.root/'current-job.json').write_text('{}')
        self.assertEqual(m.busy(self.database,self.root),{'web_task_runs':1,'worker_finalization':1})

    def test_running_functional_check_and_its_finalization_block_replacement(self):
        with sqlite3.connect(self.database)as db:
            db.execute('CREATE TABLE web_app_check_runs(status TEXT)');db.execute("INSERT INTO web_app_check_runs VALUES('running')")
        (self.root/'checks-status.json').write_text('{"runId":"fixture"}')
        self.assertEqual(m.busy(self.database,self.root),{'web_app_check_runs':1,'check_finalization':1})

    def test_project_rehearsal_and_migration_mutations_must_drain(self):
        with sqlite3.connect(self.database)as db:
            db.execute('CREATE TABLE web_project_recovery_runs(status TEXT)');db.execute("INSERT INTO web_project_recovery_runs VALUES('running')")
            db.execute('CREATE TABLE web_project_migrations(status TEXT)')
            db.executemany('INSERT INTO web_project_migrations VALUES(?)',[(state,)for state in ['planned','approved','importing','rolling_back']])
        m.PROJECT_RECOVERY_STATUS.write_text('{"runId":"fixture-recovery"}')
        self.assertEqual(m.busy(self.database,self.root),{'web_project_recovery_runs':1,'web_project_migrations':2,'project_recovery_finalization':1})

    def test_live_email_is_not_interrupted_but_stale_lease_does_not_block_forever(self):
        with sqlite3.connect(self.database)as db:
            db.execute("INSERT INTO web_notification_deliveries VALUES('sending',datetime('now'))")
            db.execute("INSERT INTO web_notification_deliveries VALUES('sending','2000-01-01')")
        self.assertEqual(m.busy(self.database,self.root),{'web_notification_deliveries':1})

    def test_raced_claim_is_resumed_before_retry(self):
        guard=m.WorkerGuard(self.database,self.root);events=[]
        with patch.object(m,'busy',side_effect=[{}, {'web_deployments':1},{},{}]),patch.object(m.time,'sleep'),patch.object(guard,'identity',return_value='birth'),patch.object(guard,'show',return_value='123'),patch.object(guard,'send',side_effect=lambda pid,birth,kind:events.append(kind)):
            with guard.quiescent():events.append('update')
        self.assertEqual(events,[signal.SIGSTOP,signal.SIGCONT,signal.SIGSTOP,'update',signal.SIGCONT])

    def test_write_failure_always_resumes_verified_worker(self):
        guard=m.WorkerGuard(self.database,self.root);events=[]
        with patch.object(m,'busy',return_value={}),patch.object(m.time,'sleep'),patch.object(guard,'identity',return_value='birth'),patch.object(guard,'show',return_value='123'),patch.object(guard,'send',side_effect=lambda pid,birth,kind:events.append(kind)):
            with self.assertRaises(RuntimeError):
                with guard.quiescent():raise RuntimeError('write failed')
        self.assertEqual(events,[signal.SIGSTOP,signal.SIGCONT])

    def test_barrier_failure_resumes_the_verified_platform_worker_without_replacement(self):
        guard=m.WorkerGuard(self.database,self.root);events=[]
        with patch.object(m,'busy',return_value={}),patch.object(m,'database_write_barrier',side_effect=RuntimeError('native writer busy')),patch.object(m.time,'sleep'),patch.object(guard,'identity',return_value='birth'),patch.object(guard,'show',return_value='123'),patch.object(guard,'send',side_effect=lambda pid,birth,kind:events.append(kind)):
            with self.assertRaises(RuntimeError):
                with guard.quiescent():events.append('must-not-replace')
        self.assertEqual(events,[signal.SIGSTOP,signal.SIGCONT])

    def test_refuses_pid_reuse_or_another_service(self):
        guard=m.WorkerGuard(self.database,self.root)
        with patch.object(guard,'identity',return_value='different'),patch.object(m.os,'kill')as kill:
            with self.assertRaises(ValueError):guard.send(123,'expected',signal.SIGSTOP)
            kill.assert_not_called()

    def test_restart_accepts_a_fast_systemd_replacement(self):
        guard=m.WorkerGuard(self.database,self.root)
        with patch.object(guard,'send'),patch.object(m.subprocess,'run'),patch.object(guard,'show',side_effect=['active','456']),patch.object(guard,'identity',return_value='new')as identity:
            guard.restart_frozen({'pid':123,'birth':'old'})
            identity.assert_called_once_with(456)


class NativeWriterBarrier(unittest.TestCase):
    def test_waits_for_earlier_writer_and_leaves_records_and_ownership_unchanged(self):
        with tempfile.TemporaryDirectory()as root:
            filename=pathlib.Path(root).resolve()/'native.db'
            with sqlite3.connect(filename)as db:db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE fixture(id TEXT)')
            owner=filename.stat().st_uid
            writer=sqlite3.connect(filename);writer.execute('BEGIN IMMEDIATE');writer.execute("INSERT INTO fixture VALUES('not-committed')")
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1)as pool:
                    future=pool.submit(m.database_write_barrier,filename)
                    with self.assertRaises(concurrent.futures.TimeoutError):future.result(timeout=.15)
                    writer.rollback();future.result(timeout=5)
            finally:writer.close()
            with sqlite3.connect(filename)as db:self.assertEqual(db.execute('SELECT count(*) FROM fixture').fetchone()[0],0)
            self.assertEqual(filename.stat().st_uid,owner)


if __name__=='__main__':unittest.main()
