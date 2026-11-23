# -*- coding: utf-8 -*-
import pymysql
c = pymysql.connect(host='127.0.0.1', port=3399, user='root', password='', charset='utf8mb4', autocommit=True)
cur = c.cursor()
cur.execute("DROP DATABASE IF EXISTS t2")
cur.execute("CREATE DATABASE t2 DEFAULT CHARACTER SET utf8mb4")
cur.execute("USE t2")
cur.execute("CREATE TABLE parent_t (id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY)")

tests = [
    ("P1 named CHECK no FK", "CREATE TABLE t1 (id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, pid INT UNSIGNED NULL, CONSTRAINT ck1 CHECK (pid IS NULL))"),
    ("P2 named CHECK with FK on same col", "CREATE TABLE t2 (id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, pid INT UNSIGNED NULL, CONSTRAINT ck2 CHECK (pid IS NULL), CONSTRAINT fk2 FOREIGN KEY (pid) REFERENCES parent_t(id))"),
    ("P3 named CHECK compound", "CREATE TABLE t3 (id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, lv TINYINT NOT NULL, pid INT UNSIGNED NULL, CONSTRAINT ck3 CHECK ((lv=1 AND pid IS NULL) OR (lv=2 AND pid IS NOT NULL)))"),
    ("P4 CHECK regxp + FK table", "CREATE TABLE t4 (id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, ph VARCHAR(20) NOT NULL, CONSTRAINT ck4 CHECK (ph REGEXP '^1[3-9][0-9]{9}$'))"),
    ("P5 plain unnamed CHECK", "CREATE TABLE t5 (id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, cap SMALLINT UNSIGNED NOT NULL, CHECK (cap > 0))"),
]
for name, sql in tests:
    try:
        cur.execute(sql); print("OK   ", name)
    except Exception as e:
        print("FAIL ", name, "=>", e)
# 用 P3 结构测试"父子一致性"的表达方式
alt = [
    ("Q1 level1 -> pid IS NULL only", "CREATE TABLE q1 (id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, lv TINYINT NOT NULL, pid INT UNSIGNED NULL, CONSTRAINT ckq1 CHECK (NOT (lv = 1 AND pid IS NOT NULL)))"),
    ("Q2 lv2 -> pid not null only", "CREATE TABLE q2 (id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, lv TINYINT NOT NULL, pid INT UNSIGNED NULL, CONSTRAINT ckq2 CHECK (NOT (lv = 2 AND pid IS NULL)))"),
]
for name, sql in alt:
    try:
        cur.execute(sql); print("OK   ", name)
    except Exception as e:
        print("FAIL ", name, "=>", e)
c.close()
