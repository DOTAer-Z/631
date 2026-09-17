#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
00_init_storage.py
目的：初始化 MySQL 元数据层、MongoDB 证据层，并为后续向量库/知识图谱准备最小元信息。

环境变量：
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=root%40123
MYSQL_DB=fault_diagnosis
MYSQL_CHARSET=utf8mb4

MONGO_URI=mongodb://127.0.0.1:27017
MONGO_DB=fault_diagnosis

用法：
  python3 00_init_storage.py
  python3 00_init_storage.py --drop-mongo-indexes-only
"""

from __future__ import annotations

import argparse
import os
import sys
from textwrap import dedent

import pymysql
from pymysql.cursors import DictCursor
from pymongo import ASCENDING, MongoClient

from config import (
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_USER,
    MYSQL_PASSWORD,
    MYSQL_DB,
    MYSQL_CHARSET, 
    MONGO_URI,
    MONGO_DB,
    MANIFEST_PATH,
    VECTOR_OUT_DIR,
    KG_OUT_DIR,
    ensure_dirs,
    print_config,
)

ensure_dirs()
print_config()


def die(msg: str, code: int = 1) -> None:
    print(f"[FATAL] {msg}", file=sys.stderr)
    raise SystemExit(code)


def info(msg: str) -> None:
    print(f"[INFO] {msg}")


MYSQL_DDL = [
    dedent(
        """
        CREATE TABLE IF NOT EXISTS systems (
          system_id        VARCHAR(128) PRIMARY KEY,
          system_code      VARCHAR(128) NOT NULL UNIQUE,
          system_name      VARCHAR(255) NOT NULL,
          system_desc      TEXT NULL,
          version          VARCHAR(128) NULL,
          status           VARCHAR(32) NOT NULL DEFAULT 'active',
          created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX idx_system_name (system_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS subsystems (
          subsystem_id     BIGINT PRIMARY KEY AUTO_INCREMENT,
          system_id        VARCHAR(128) NOT NULL,
          subsystem_code   VARCHAR(128) NOT NULL,
          subsystem_name   VARCHAR(255) NOT NULL,
          subsystem_desc   TEXT NULL,
          created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_system_subsystem_code (system_id, subsystem_code),
          KEY idx_subsystem_name (subsystem_name),
          CONSTRAINT fk_subsystems_system FOREIGN KEY (system_id)
            REFERENCES systems(system_id)
            ON UPDATE CASCADE ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS components (
          component_id      BIGINT PRIMARY KEY AUTO_INCREMENT,
          system_id         VARCHAR(128) NOT NULL,
          subsystem_id      BIGINT NULL,
          component_code    VARCHAR(255) NOT NULL,
          component_name    VARCHAR(255) NOT NULL,
          component_path    VARCHAR(1024) NULL,
          component_type    VARCHAR(64) NOT NULL DEFAULT 'code_file',
          owner             VARCHAR(255) NULL,
          status            VARCHAR(32) NOT NULL DEFAULT 'active',
          created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_component_code (system_id, component_code),
          KEY idx_component_path (component_path(255)),
          CONSTRAINT fk_components_system FOREIGN KEY (system_id)
            REFERENCES systems(system_id)
            ON UPDATE CASCADE ON DELETE RESTRICT,
          CONSTRAINT fk_components_subsystem FOREIGN KEY (subsystem_id)
            REFERENCES subsystems(subsystem_id)
            ON UPDATE CASCADE ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS cases (
          case_id               VARCHAR(255) PRIMARY KEY,
          system_id             VARCHAR(128) NOT NULL,
          subsystem_id          BIGINT NULL,
          case_code             VARCHAR(255) NOT NULL,
          case_name             VARCHAR(255) NOT NULL,
          case_desc             TEXT NULL,
          test_name             VARCHAR(255) NOT NULL,
          is_fault              TINYINT(1) NOT NULL DEFAULT 0,
          fault_type            VARCHAR(255) NULL,
          root_cause_type       VARCHAR(255) NULL,
          target_component_id   BIGINT NULL,
          target_component_path VARCHAR(1024) NULL,
          target_function       VARCHAR(255) NULL,
          injection_point       TEXT NULL,
          fip_info_json         JSON NULL,
          root_cause_json       JSON NULL,
          created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_case_code (case_code),
          KEY idx_cases_system_subsystem (system_id, subsystem_id),
          KEY idx_cases_fault_type (fault_type),
          KEY idx_cases_root_cause_type (root_cause_type),
          CONSTRAINT fk_cases_system FOREIGN KEY (system_id)
            REFERENCES systems(system_id)
            ON UPDATE CASCADE ON DELETE RESTRICT,
          CONSTRAINT fk_cases_subsystem FOREIGN KEY (subsystem_id)
            REFERENCES subsystems(subsystem_id)
            ON UPDATE CASCADE ON DELETE SET NULL,
          CONSTRAINT fk_cases_component FOREIGN KEY (target_component_id)
            REFERENCES components(component_id)
            ON UPDATE CASCADE ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS runs (
          run_id               VARCHAR(255) PRIMARY KEY,
          case_id              VARCHAR(255) NOT NULL,
          system_id            VARCHAR(128) NOT NULL,
          round_no             INT NOT NULL,
          run_name             VARCHAR(255) NULL,
          status               VARCHAR(32) NOT NULL DEFAULT 'ready',
          is_fault             TINYINT(1) NOT NULL,
          log_dir              VARCHAR(2048) NOT NULL,
          start_time           DATETIME(6) NULL,
          end_time             DATETIME(6) NULL,
          total_logs           INT NOT NULL DEFAULT 0,
          error_logs           INT NOT NULL DEFAULT 0,
          critical_logs        INT NOT NULL DEFAULT 0,
          window_count         INT NOT NULL DEFAULT 0,
          stats_json           JSON NULL,
          created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_case_round (case_id, round_no),
          KEY idx_runs_case_time (case_id, start_time),
          KEY idx_runs_status (status),
          CONSTRAINT fk_runs_case FOREIGN KEY (case_id)
            REFERENCES cases(case_id)
            ON UPDATE CASCADE ON DELETE CASCADE,
          CONSTRAINT fk_runs_system FOREIGN KEY (system_id)
            REFERENCES systems(system_id)
            ON UPDATE CASCADE ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS labels (
          label_id            BIGINT PRIMARY KEY AUTO_INCREMENT,
          label_type          VARCHAR(64) NOT NULL,
          label_code          VARCHAR(255) NOT NULL,
          label_name          VARCHAR(255) NOT NULL,
          label_desc          TEXT NULL,
          parent_label_id     BIGINT NULL,
          status              VARCHAR(32) NOT NULL DEFAULT 'active',
          created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_label_type_code (label_type, label_code),
          KEY idx_label_type_name (label_type, label_name),
          CONSTRAINT fk_labels_parent FOREIGN KEY (parent_label_id)
            REFERENCES labels(label_id)
            ON UPDATE CASCADE ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS object_labels (
          object_label_id      BIGINT PRIMARY KEY AUTO_INCREMENT,
          object_type          VARCHAR(64) NOT NULL,
          object_id            VARCHAR(255) NOT NULL,
          label_id             BIGINT NOT NULL,
          source               VARCHAR(64) NOT NULL,
          confidence           DECIMAL(8, 6) NULL,
          payload_json         JSON NULL,
          created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE KEY uq_object_label (object_type, object_id, label_id, source),
          KEY idx_object_type_id (object_type, object_id),
          CONSTRAINT fk_object_labels_label FOREIGN KEY (label_id)
            REFERENCES labels(label_id)
            ON UPDATE CASCADE ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS ingestions (
          ingestion_id         BIGINT PRIMARY KEY AUTO_INCREMENT,
          dataset_version      VARCHAR(255) NOT NULL,
          script_version       VARCHAR(255) NULL,
          source_type          VARCHAR(64) NOT NULL DEFAULT 'dataset',
          source_name          VARCHAR(255) NULL,
          source_path          VARCHAR(2048) NULL,
          trigger_mode         VARCHAR(64) NOT NULL DEFAULT 'manual',
          status               VARCHAR(32) NOT NULL DEFAULT 'running',
          started_at           DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
          finished_at          DATETIME(6) NULL,
          inserted_log_count   INT NOT NULL DEFAULT 0,
          inserted_window_count INT NOT NULL DEFAULT 0,
          inserted_embedding_count INT NOT NULL DEFAULT 0,
          error_message        TEXT NULL,
          operator_name        VARCHAR(255) NULL,
          created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_ingestion_status (status),
          KEY idx_ingestion_started_at (started_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS diagnosis_tasks (
          task_id              VARCHAR(255) PRIMARY KEY,
          task_code            VARCHAR(255) NOT NULL,
          task_type            VARCHAR(64) NOT NULL DEFAULT 'fault_diagnosis',
          source_mode          VARCHAR(64) NOT NULL DEFAULT 'log',
          system_id            VARCHAR(128) NOT NULL,
          subsystem_id         BIGINT NULL,
          component_id         BIGINT NULL,
          case_id              VARCHAR(255) NULL,
          run_id               VARCHAR(255) NULL,
          status               VARCHAR(32) NOT NULL DEFAULT 'pending',
          priority             VARCHAR(32) NOT NULL DEFAULT 'normal',
          created_by           VARCHAR(255) NULL,
          trigger_source       VARCHAR(64) NOT NULL DEFAULT 'dataset',
          task_desc            TEXT NULL,
          created_at           DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
          started_at           DATETIME(6) NULL,
          finished_at          DATETIME(6) NULL,
          UNIQUE KEY uq_task_code (task_code),
          KEY idx_task_status (status),
          KEY idx_task_case_run (case_id, run_id),
          CONSTRAINT fk_task_system FOREIGN KEY (system_id)
            REFERENCES systems(system_id)
            ON UPDATE CASCADE ON DELETE RESTRICT,
          CONSTRAINT fk_task_subsystem FOREIGN KEY (subsystem_id)
            REFERENCES subsystems(subsystem_id)
            ON UPDATE CASCADE ON DELETE SET NULL,
          CONSTRAINT fk_task_component FOREIGN KEY (component_id)
            REFERENCES components(component_id)
            ON UPDATE CASCADE ON DELETE SET NULL,
          CONSTRAINT fk_task_case FOREIGN KEY (case_id)
            REFERENCES cases(case_id)
            ON UPDATE CASCADE ON DELETE SET NULL,
          CONSTRAINT fk_task_run FOREIGN KEY (run_id)
            REFERENCES runs(run_id)
            ON UPDATE CASCADE ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS task_inputs (
          task_input_id        BIGINT PRIMARY KEY AUTO_INCREMENT,
          task_id              VARCHAR(255) NOT NULL,
          input_type           VARCHAR(64) NOT NULL,
          input_count          INT NOT NULL DEFAULT 1,
          mongo_ref_id         VARCHAR(255) NULL,
          summary_text         TEXT NULL,
          created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_task_inputs_task (task_id),
          CONSTRAINT fk_task_inputs_task FOREIGN KEY (task_id)
            REFERENCES diagnosis_tasks(task_id)
            ON UPDATE CASCADE ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS task_feature_summaries (
          feature_id           BIGINT PRIMARY KEY AUTO_INCREMENT,
          task_id              VARCHAR(255) NOT NULL,
          feature_type         VARCHAR(64) NOT NULL,
          feature_name         VARCHAR(255) NOT NULL,
          feature_value        TEXT NOT NULL,
          value_type           VARCHAR(64) NOT NULL DEFAULT 'string',
          confidence           DECIMAL(8, 6) NULL,
          source_evidence_id   VARCHAR(255) NULL,
          created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_task_feature_task (task_id),
          KEY idx_task_feature_name (feature_type, feature_name),
          CONSTRAINT fk_feature_task FOREIGN KEY (task_id)
            REFERENCES diagnosis_tasks(task_id)
            ON UPDATE CASCADE ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS task_metric_features (
          metric_feature_id    BIGINT PRIMARY KEY AUTO_INCREMENT,
          task_id              VARCHAR(255) NOT NULL,
          metric_code          VARCHAR(255) NOT NULL,
          window_start         DATETIME(6) NULL,
          window_end           DATETIME(6) NULL,
          avg_value            DOUBLE NULL,
          max_value            DOUBLE NULL,
          min_value            DOUBLE NULL,
          anomaly_count        INT NOT NULL DEFAULT 0,
          trend                VARCHAR(64) NULL,
          summary_json         JSON NULL,
          created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_task_metric_task (task_id),
          KEY idx_task_metric_code (metric_code),
          CONSTRAINT fk_metric_feature_task FOREIGN KEY (task_id)
            REFERENCES diagnosis_tasks(task_id)
            ON UPDATE CASCADE ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS diagnosis_results (
          result_id            BIGINT PRIMARY KEY AUTO_INCREMENT,
          task_id              VARCHAR(255) NOT NULL,
          result_version       INT NOT NULL DEFAULT 1,
          is_fault             TINYINT(1) NULL,
          fault_type           VARCHAR(255) NULL,
          root_cause           TEXT NULL,
          root_cause_type      VARCHAR(255) NULL,
          target_component_id  BIGINT NULL,
          target_function      VARCHAR(255) NULL,
          severity             VARCHAR(64) NULL,
          confidence           DECIMAL(8, 6) NULL,
          recommendation       TEXT NULL,
          explanation_text     LONGTEXT NULL,
          decision_path        TEXT NULL,
          result_status        VARCHAR(32) NOT NULL DEFAULT 'draft',
          created_at           DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
          updated_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_task_result_version (task_id, result_version),
          KEY idx_result_fault_type (fault_type),
          CONSTRAINT fk_result_task FOREIGN KEY (task_id)
            REFERENCES diagnosis_tasks(task_id)
            ON UPDATE CASCADE ON DELETE CASCADE,
          CONSTRAINT fk_result_component FOREIGN KEY (target_component_id)
            REFERENCES components(component_id)
            ON UPDATE CASCADE ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS result_evidence_refs (
          ref_id               BIGINT PRIMARY KEY AUTO_INCREMENT,
          result_id            BIGINT NOT NULL,
          evidence_type        VARCHAR(64) NOT NULL,
          mongo_ref_id         VARCHAR(255) NULL,
          score                DOUBLE NULL,
          role_name            VARCHAR(64) NULL,
          created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_result_ref_result (result_id),
          CONSTRAINT fk_result_ref_result FOREIGN KEY (result_id)
            REFERENCES diagnosis_results(result_id)
            ON UPDATE CASCADE ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS result_writebacks (
          writeback_id         BIGINT PRIMARY KEY AUTO_INCREMENT,
          result_id            BIGINT NOT NULL,
          target_system        VARCHAR(255) NOT NULL,
          target_table_or_api  VARCHAR(255) NOT NULL,
          writeback_status     VARCHAR(32) NOT NULL DEFAULT 'pending',
          request_payload      LONGTEXT NULL,
          response_payload     LONGTEXT NULL,
          error_message        TEXT NULL,
          written_at           DATETIME(6) NULL,
          created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_writeback_status (writeback_status),
          CONSTRAINT fk_writeback_result FOREIGN KEY (result_id)
            REFERENCES diagnosis_results(result_id)
            ON UPDATE CASCADE ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS metric_definitions (
          metric_def_id        BIGINT PRIMARY KEY AUTO_INCREMENT,
          metric_code          VARCHAR(255) NOT NULL,
          metric_name          VARCHAR(255) NOT NULL,
          metric_category      VARCHAR(128) NULL,
          unit                 VARCHAR(64) NULL,
          description          TEXT NULL,
          normal_range_min     DOUBLE NULL,
          normal_range_max     DOUBLE NULL,
          component_scope      VARCHAR(255) NULL,
          created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_metric_code (metric_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS model_versions (
          model_version_id     BIGINT PRIMARY KEY AUTO_INCREMENT,
          model_type           VARCHAR(64) NOT NULL,
          model_name           VARCHAR(255) NOT NULL,
          model_version        VARCHAR(255) NOT NULL,
          provider             VARCHAR(255) NULL,
          status               VARCHAR(32) NOT NULL DEFAULT 'active',
          created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE KEY uq_model_version (model_type, model_name, model_version)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
    dedent(
        """
        CREATE TABLE IF NOT EXISTS task_execution_records (
          exec_id              BIGINT PRIMARY KEY AUTO_INCREMENT,
          task_id              VARCHAR(255) NOT NULL,
          stage_name           VARCHAR(64) NOT NULL,
          strategy_name        VARCHAR(64) NOT NULL,
          model_version_id     BIGINT NULL,
          status               VARCHAR(32) NOT NULL DEFAULT 'pending',
          started_at           DATETIME(6) NULL,
          finished_at          DATETIME(6) NULL,
          remark               TEXT NULL,
          created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_exec_task (task_id),
          CONSTRAINT fk_exec_task FOREIGN KEY (task_id)
            REFERENCES diagnosis_tasks(task_id)
            ON UPDATE CASCADE ON DELETE CASCADE,
          CONSTRAINT fk_exec_model FOREIGN KEY (model_version_id)
            REFERENCES model_versions(model_version_id)
            ON UPDATE CASCADE ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    ),
]


MONGO_COLLECTIONS = {
    "evidence_inputs": [
        ([('task_id', ASCENDING), ('input_type', ASCENDING), ('created_at', ASCENDING)], 'idx_task_input_type_time'),
        ([('source_type', ASCENDING)], 'idx_source_type'),
    ],
    "uploaded_files": [
        ([('task_id', ASCENDING), ('created_at', ASCENDING)], 'idx_uploaded_task_time'),
    ],
    "log_entries": [
        ([('run_id', ASCENDING), ('timestamp', ASCENDING)], 'idx_run_time'),
        ([('run_id', ASCENDING), ('level', ASCENDING)], 'idx_run_level'),
        ([('run_id', ASCENDING), ('module', ASCENDING)], 'idx_run_module'),
        ([('case_id', ASCENDING)], 'idx_case'),
        ([('system_id', ASCENDING), ('subsystem', ASCENDING)], 'idx_system_subsystem'),
    ],
    "log_windows": [
        ([('run_id', ASCENDING), ('start_time', ASCENDING)], 'idx_windows_run_start'),
        ([('case_id', ASCENDING)], 'idx_windows_case'),
        ([('system_id', ASCENDING), ('subsystem', ASCENDING)], 'idx_windows_system_subsystem'),
    ],
    "text_descriptions": [
        ([('task_id', ASCENDING), ('created_at', ASCENDING)], 'idx_text_desc_task_time'),
    ],
    "metric_series": [
        ([('task_id', ASCENDING), ('metric_code', ASCENDING), ('window_start', ASCENDING)], 'idx_metric_series_task_code_time'),
    ],
    "metric_anomalies": [
        ([('task_id', ASCENDING), ('metric_code', ASCENDING), ('start_time', ASCENDING)], 'idx_metric_anomaly_task_code_time'),
    ],
    "retrieval_results": [
        ([('task_id', ASCENDING), ('created_at', ASCENDING)], 'idx_retrieval_task_time'),
    ],
    "llm_traces": [
        ([('task_id', ASCENDING), ('created_at', ASCENDING)], 'idx_llm_task_time'),
    ],
}


TEXT_INDEXES = {
    "log_entries": [('message', 'text'), ('raw_line', 'text')],
    "log_windows": [('text', 'text')],
    "text_descriptions": [('raw_text', 'text'), ('normalized_text', 'text')],
}


def connect_mysql_server(db: str | None = None):
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=db,
        charset=MYSQL_CHARSET,
        autocommit=True,
        cursorclass=DictCursor,
    )


def mysql_init() -> None:
    info(f"Connecting MySQL server {MYSQL_HOST}:{MYSQL_PORT} ...")
    conn = connect_mysql_server(None)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DB}` CHARACTER SET {MYSQL_CHARSET} COLLATE {MYSQL_CHARSET}_unicode_ci")
    finally:
        conn.close()

    conn = connect_mysql_server(MYSQL_DB)
    try:
        with conn.cursor() as cur:
            info(f"Creating MySQL tables in `{MYSQL_DB}` ...")
            for ddl in MYSQL_DDL:
                cur.execute(ddl)
    finally:
        conn.close()
    info("MySQL init done.")


def mongo_init(drop_indexes_only: bool = False) -> None:
    info(f"Connecting MongoDB {MONGO_URI} ...")
    client = MongoClient(MONGO_URI)
    try:
        db = client[MONGO_DB]
        for col_name, indexes in MONGO_COLLECTIONS.items():
            col = db[col_name]
            if drop_indexes_only:
                for name in col.index_information().keys():
                    if name != '_id_':
                        col.drop_index(name)
                continue

            for keys, name in indexes:
                col.create_index(keys, name=name)

        if not drop_indexes_only:
            for col_name, keys in TEXT_INDEXES.items():
                db[col_name].create_index(keys, name=f"idx_text_{col_name}")
    finally:
        client.close()
    info("MongoDB init done.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Initialize MySQL + MongoDB storage for the new pipeline.")
    ap.add_argument('--drop-mongo-indexes-only', action='store_true', help='Drop non-_id Mongo indexes and exit.')
    args = ap.parse_args()

    mysql_init()
    mongo_init(drop_indexes_only=args.drop_mongo_indexes_only)


if __name__ == '__main__':
    main()
