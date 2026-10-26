# Unit tests for PySpark and Delta code

## Contents
- Session fixture
- Keep logic out of the IO
- Building input
- Asserting on results
- Tables and storage
- Patching the session

## Session fixture

Start one local session per test run and share it. Put it in `conftest.py`:

```python
@pytest.fixture(scope="session")
def spark(tmp_path_factory):
    builder = (
        SparkSession.builder.master("local[1]")
        .appName("unit-tests")
        .config("spark.sql.shuffle.partitions", "1")   # tiny data, avoid 200 empty tasks
        .config("spark.ui.enabled", "false")
        .config("spark.sql.warehouse.dir", str(tmp_path_factory.mktemp("warehouse")))
    )
    session = builder.getOrCreate()
    yield session
    session.stop()
```

For Delta, add the Delta package and these two configs (use the Delta version matching the Spark version):
`spark.sql.extensions=io.delta.sql.DeltaSparkSessionExtension` and
`spark.sql.catalog.spark_catalog=org.apache.spark.sql.delta.catalog.DeltaCatalog`.

Never create a `SparkSession` inside a test. Session startup takes seconds and makes the suite slow.

## Keep logic out of the IO

Spark tests are slow compared to plain Python. Put decisions (parsing, validation, config loading, query building, status evaluation) in pure functions and test those with plain pytest. Keep Spark tests for the DataFrame transformations and writes themselves.

## Building input

- Pass an explicit schema or column list: `spark.createDataFrame(rows, schema=["id", "value"])` or a DDL string `"id INT, value STRING"`. Inference of `None`-only columns fails and integers infer as `bigint`.
- Keep inputs to a handful of rows that each exist for a reason (duplicate key, null, boundary).

## Asserting on results

- `collect()` order is not guaranteed. Sort first (`df.orderBy("id").collect()`) or compare sets.
- Column order is not a contract unless the code promises it. Use `df.select(sorted(df.columns))`.
- To check shape, assert on `df.schema.simpleString()` or `df.columns`.
- With PySpark 3.5 or later, `pyspark.testing.assertDataFrameEqual(actual, expected)` compares schema and rows with useful diffs.
- Compare against `Row(...)` objects or a second small DataFrame.
- For failures, use `pytest.raises` around the action. Spark is lazy, so call `.collect()` or `.count()` inside the `with` block to force execution.

## Tables and storage

- Write tables under a temporary location, not `/tmp/<name>` or the repo's `spark-warehouse`:
  `df.write.format("delta").save(str(tmp_path / "t"))` and `spark.read.format("delta").load(...)`.
- For named tables, use a unique name per test (include the test name) and drop it in a fixture teardown:

```python
@pytest.fixture
def table_name(spark, request):
    name = f"t_{request.node.name}"
    yield name
    spark.sql(f"DROP TABLE IF EXISTS {name}")
```

- Do not repeat `DROP TABLE` and `rmtree` boilerplate at the top of every test. One fixture does it.
- Test each write mode (append, overwrite, merge/upsert) with a second write over existing data. A first write into an empty table hides most merge bugs.
- Timestamps added at write time: freeze time with `freezegun` and assert the exact value.

## Patching the session

Code that fetches its session through a helper (`get_spark()`) is patched where it is used, once per module that calls it:

```python
@patch("mypkg.writer.get_spark")
def test_overwrite(mock_get_spark, spark):
    mock_get_spark.return_value = spark
```

When one test needs the same patch in several modules, use a fixture or `monkeypatch.setattr` in a loop inside the fixture instead of stacking decorators. Better: let the function accept the session as a parameter.
