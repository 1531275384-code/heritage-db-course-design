# -*- coding: utf-8 -*-
import pymysql
c = pymysql.connect(host='127.0.0.1', port=3399, user='root', password='', charset='utf8mb4', autocommit=True)
cur = c.cursor()
cur.execute("DROP DATABASE IF EXISTS t1")
cur.execute("CREATE DATABASE t1 DEFAULT CHARACTER SET utf8mb4")
cur.execute("USE t1")

tests = [
    ("A: CHECK col IS NULL", "CREATE TABLE a1 (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, pid INT NULL, ck CHECK (pid IS NULL))"),
    ("B: CHECK IN", "CREATE TABLE a2 (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, lv TINYINT NOT NULL, ck CHECK (lv IN (1,2)))"),
    ("C: CHECK AND/OR", "CREATE TABLE a3 (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, lv TINYINT NOT NULL, pid INT NULL, ck CHECK ((lv=1 AND pid IS NULL) OR (lv=2 AND pid IS NOT NULL)))"),
    ("D: CHECK REGEXP", "CREATE TABLE a4 (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, ph VARCHAR(20) NOT NULL, ck CHECK (ph REGEXP '^1[3-9][0-9]{9}$'))"),
    ("E: CHECK CHAR_LENGTH IN", "CREATE TABLE a5 (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, ic VARCHAR(18) NOT NULL, ck CHECK (CHAR_LENGTH(ic) IN (15,18)))"),
    ("F: CHECK with FK column", "CREATE TABLE a6 (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, pid INT NULL, ck CHECK (pid IS NULL), FOREIGN KEY (pid) REFERENCES a1(id))"),
    ("G: CHECK date const", "CREATE TABLE a7 (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, bd DATE NOT NULL, ck CHECK (bd >= '1900-01-01'))"),
    ("H: CHECK numeric", "CREATE TABLE a8 (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, cap SMALLINT UNSIGNED NOT NULL, ck CHECK (cap > 0))"),
    ("I: CHECK end>start", "CREATE TABLE a9 (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, s DATETIME NOT NULL, e DATETIME NOT NULL, d DATETIME NOT NULL, ck CHECK (e > s AND d <= s))"),
    ("J: table-level CHECK name", "CREATE TABLE a10 (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, lv TINYINT NOT NULL, CONSTRAINT ck_lv CHECK (lv IN (1,2)))"),
    ("K: CHECK with two refs same table", "CREATE TABLE a11 (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, lv TINYINT NOT NULL, pid INT NULL, CONSTRAINT ck_kk CHECK (lv = 1 OR pid IS NOT NULL))"),
]
for name, sql in tests:
    try:
        cur.execute(sql)
        print("OK   ", name)
    except Exception as e:
        print("FAIL ", name, "=>", e)
c.close()
