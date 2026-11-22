# 数据源连接器使用说明

## 概述

平台提供八类数据源连接器，统一实现 `IDataSourceConnector` 接口。

## 连接器类型

### 通用数据源

| 类型 | 标识 | 说明 |
| --- | --- | --- |
| CSV | csv | CSV 文件读取 |
| JSON | json | JSON 文件读取 |
| REST API | rest_api | REST API 端点调用 |
| 数据库 | database | SQL 数据库只读查询 |

### 大数据组件

| 类型 | 标识 | 说明 |
| --- | --- | --- |
| Hologres | hologres | Hologres 实时数仓（PG 协议兼容） |
| Kafka | kafka | Kafka 消息流消费 |
| Flink | flink | Flink 流计算引擎（JobManager REST API） |
| Spark | spark | Spark 批计算引擎（Livy REST API） |

## 配置示例

### Hologres

```yaml
- node_id: read_hologres
  node_type: data_source
  configuration:
    connection_reference: access_id
    query: "SELECT * FROM sales WHERE dt >= current_date - 7"
```

环境变量：
```
HOLOGRES_ENDPOINT=hgproxy.example.com
HOLOGRES_PORT=80
HOLOGRES_DATABASE=analytics
HOLOGRES_ACCESS_ID=your_access_id
HOLOGRES_ACCESS_KEY=your_access_key
```

### Kafka

```yaml
- node_id: consume_kafka
  node_type: data_source
  configuration:
    connection_reference: kafka_brokers
    query: ""
    dataset_identifier: events
```

环境变量：
```
KAFKA_BROKERS=broker1:9092,broker2:9092
KAFKA_TOPIC=events_topic
KAFKA_GROUP_ID=consumer_group
```

### Flink

```yaml
- node_id: flink_job
  node_type: data_source
  configuration:
    connection_reference: flink_jm
    query: ""
```

环境变量：
```
FLINK_JOBMANAGER_URL=http://flink-host:8081
FLINK_JAR_ID=your_jar_id
```

### Spark

```yaml
- node_id: spark_job
  node_type: data_source
  configuration:
    connection_reference: spark_master
    query: "spark.sql('SELECT * FROM table').collect()"
```

环境变量：
```
SPARK_MASTER_URL=spark://spark-host:7077
SPARK_LIVY_URL=http://spark-host:8998
```

## 凭据保护

连接配置中的密钥/令牌不出现在日志或异常消息中。Hologres 连接器在异常中仅输出错误类型名，不暴露连接串。

## 分页与流式

所有连接器支持分页读取（offset/limit 与游标两种模式）。Kafka 连接器支持流式消费，分页语义为单次拉取最大消息数。

## Schema 推断

连接器支持从数据推断 Schema（字段名与类型映射）。Hologres 通过 `information_schema` 查询表结构；Kafka 从首条 JSON 消息推断字段。

## 扩展点

新增数据源类型只需实现 `IDataSourceConnector` 接口并注册至 `DataSourceConnectorRegistry`。
