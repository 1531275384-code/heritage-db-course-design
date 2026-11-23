# -*- coding: utf-8 -*-
import pymysql
c = pymysql.connect(host='127.0.0.1', port=3399, user='root', password='', charset='utf8mb4', autocommit=True)
cur = c.cursor()
cur.execute("DROP DATABASE IF EXISTS t3")
cur.execute("CREATE DATABASE t3 DEFAULT CHARACTER SET utf8mb4")
cur.execute("USE t3")

tests = [
 ("R1 self-ref FK + CHECK on fk col (FK先声明)", """
CREATE TABLE r1 (
  category_id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  category_level TINYINT NOT NULL DEFAULT 1,
  parent_id INT UNSIGNED NULL,
  CONSTRAINT fk_r1 FOREIGN KEY (parent_id) REFERENCES r1(category_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT ck_r1 CHECK ((category_level = 1 AND parent_id IS NULL) OR (category_level = 2 AND parent_id IS NOT NULL))
) ENGINE=InnoDB"""),
 ("R2 self-ref FK + CHECK 仅单列", """
CREATE TABLE r2 (
  category_id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  category_level TINYINT NOT NULL DEFAULT 1,
  parent_id INT UNSIGNED NULL,
  CONSTRAINT fk_r2 FOREIGN KEY (parent_id) REFERENCES r2(category_id),
  CONSTRAINT ck_r2 CHECK (parent_id IS NULL)
) ENGINE=InnoDB"""),
 ("R3 自引用FK 无CHECK", """
CREATE TABLE r3 (
  category_id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  parent_id INT UNSIGNED NULL,
  CONSTRAINT fk_r3 FOREIGN KEY (parent_id) REFERENCES r3(category_id)
) ENGINE=InnoDB"""),
 ("R4 有CHECK无FK", """
CREATE TABLE r4 (
  category_id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  category_level TINYINT NOT NULL DEFAULT 1,
  parent_id INT UNSIGNED NULL,
  CONSTRAINT ck_r4 CHECK ((category_level = 1 AND parent_id IS NULL) OR (category_level = 2 AND parent_id IS NOT NULL))
) ENGINE=InnoDB"""),
]
for name, sql in tests:
    try:
        cur.execute(sql); print("OK   ", name)
    except Exception as e:
        print("FAIL ", name, "=>", e)
c.close()
