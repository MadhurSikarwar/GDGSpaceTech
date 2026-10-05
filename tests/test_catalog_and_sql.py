"""SQL script splitting and the catalogue-to-schema mapping (no database needed)."""
from datetime import date, datetime

from orbitwatch import config
from orbitwatch.jobs.catalog import build_rows, parse_gcat_date
from orbitwatch.mysql_setup import split_sql


def test_split_sql_honours_delimiter_blocks():
    script = """-- comment
CREATE TABLE a (x INT);
DELIMITER $$
CREATE TRIGGER t BEFORE INSERT ON a FOR EACH ROW
BEGIN
    IF NEW.x < 0 THEN SET NEW.x = 0; END IF;
END$$
DELIMITER ;
INSERT INTO a VALUES (1),
    (2);
"""
    stmts = split_sql(script)
    assert len(stmts) == 3
    assert stmts[1].startswith("CREATE TRIGGER") and stmts[1].rstrip().endswith("END")
    assert "SET NEW.x = 0;" in stmts[1]


def test_schema_files_split_into_expected_statements():
    counts = {name: len(split_sql((config.SQL_DIR / name).read_text(encoding="utf-8")))
              for name in ("01_schema.sql", "02_routines.sql", "03_views.sql")}
    assert counts["01_schema.sql"] == 21          # 21 tables
    assert counts["03_views.sql"] == 9            # 9 views
    assert counts["02_routines.sql"] == 22        # 11 drops + 3 routines + 8 triggers


def test_parse_gcat_dates():
    assert parse_gcat_date("1957 Oct  4 1928:34") == datetime(1957, 10, 4, 19, 28, 34)  # HHMM:SS
    assert parse_gcat_date("2017 May") == datetime(2017, 5, 1)
    assert parse_gcat_date("1958 Jan  4?") == datetime(1958, 1, 4)
    assert parse_gcat_date("-") is None


def _sources():
    satcat = [
        {"OBJECT_NAME": "INMARSAT 4-F1", "OBJECT_ID": "2005-009A", "NORAD_CAT_ID": "28628", "OBJECT_TYPE": "PAY",
         "OPS_STATUS_CODE": "+", "OWNER": "IM", "LAUNCH_DATE": "2005-03-11", "DECAY_DATE": ""},
        {"OBJECT_NAME": "SAT-X", "OBJECT_ID": "2020-001A", "NORAD_CAT_ID": "45000", "OBJECT_TYPE": "PAY",
         "OPS_STATUS_CODE": "D", "OWNER": "IND", "LAUNCH_DATE": "2020-01-05", "DECAY_DATE": "2024-02-01"},
        {"OBJECT_NAME": "SAT-X DEB", "OBJECT_ID": "2020-001C", "NORAD_CAT_ID": "45002", "OBJECT_TYPE": "DEB",
         "OPS_STATUS_CODE": "", "OWNER": "IND", "LAUNCH_DATE": "2020-01-05", "DECAY_DATE": ""},
    ]
    gcat = {
        "orgs": [
            {"Code": "IN", "Type": "CY", "Class": "C", "StateCode": "IN", "ShortName": "India", "Name": "Republic of India",
             "ShortEName": "India", "EName": "India, Republic of"},
            {"Code": "GB", "Type": "CY", "Class": "C", "StateCode": "GB", "ShortName": "UK", "Name": "United Kingdom",
             "ShortEName": "UK", "EName": "United Kingdom"},
            {"Code": "ISRO", "Type": "O", "Class": "C", "StateCode": "IN", "Name": "Indian Space Research Organization", "EName": "-"},
            {"Code": "INMRL", "Type": "O", "Class": "B", "StateCode": "GB", "Name": "Inmarsat Ltd", "EName": "-"},
            {"Code": "INMRV", "Type": "O", "Class": "B", "StateCode": "GB", "Name": "Inmarsat (Viasat)", "EName": "-"},
        ],
        "sites": [{"Site": "SHAR", "StateCode": "IN", "Name": "Sriharikota", "EName": "-", "Latitude": "13.7", "Longitude": "80.2"}],
        "lv": [{"LV_Name": "PSLV", "LV_Manufacturer": "ISRO"}],
        "launchlog": [
            {"Launch_Tag": "2020-001", "Launch_Date": "2020 Jan  5 0400", "LV_Type": "PSLV", "Launch_Site": "SHAR", "Launch_Code": "OS"},
            {"Launch_Tag": "2005-009", "Launch_Date": "2005 Mar 11 0042", "LV_Type": "Zenit-3SL", "Launch_Site": "ODYS", "Launch_Code": "OS"},
            {"Launch_Tag": "1958-F01", "Launch_Date": "1958 Feb  3", "LV_Type": "-", "Launch_Site": "-", "Launch_Code": "OF"},
        ],
        "satcat": [
            {"JCAT": "S28628", "Satcat": "28628", "Launch_Tag": "2005-009", "Parent": "-", "Owner": "INMRL", "LDate": "2005 Mar 11"},
            {"JCAT": "S45000", "Satcat": "45000", "Launch_Tag": "2020-001", "Parent": "-", "Owner": "ISRO", "LDate": "2020 Jan  5"},
            {"JCAT": "S45002", "Satcat": "45002", "Launch_Tag": "2020-001", "Parent": "S45000", "Owner": "ISRO", "LDate": "2020 Jan  5"},
            {"JCAT": "A99999", "Satcat": "NNA", "Launch_Tag": "2020-001", "Parent": "-", "Owner": "ISRO", "LDate": "2020 Jan  5"},
        ],
        "psatcat": [{"JCAT": "S45000", "Program": "SAT-X", "Category": "IMG"}],
    }
    return satcat, gcat


def test_build_rows_maps_sources_onto_the_schema():
    rows = build_rows(*_sources())
    objects = {o[0]: o for o in rows["objects"]}
    assert objects[45000][5] == "Decayed" and objects[45000][6] == "2024-02-01"
    assert objects[45002][3] == "Debris" and objects[45002][4] == "2020-001"
    assert rows["parents"] == [(45002, 45000)]                        # recursive relationship
    launches = {l[0]: l for l in rows["launches"]}
    assert launches["1958-F01"][4] == "Failure" and launches["2020-001"][2] == "SHAR"
    assert dict(rows["vehicles"])["PSLV"] == "ISRO"
    orgs = {o[0]: o for o in rows["organisations"]}
    assert orgs["ISRO"][2] == "Government" and orgs["INMRL"][2] == "Private" and orgs["ISRO"][3] == "IN"
    assert ("SAT-X", "Earth imaging", "ISRO") in rows["missions"]


def test_curated_ownership_transfer_splits_the_period():
    rows = build_rows(*_sources())
    inmarsat = sorted(r for r in rows["ownership"] if r[0] == 28628)
    assert inmarsat == [(28628, "INMRL", date(2005, 3, 11), date(2023, 5, 31)),
                        (28628, "INMRV", date(2023, 5, 31), None)]
