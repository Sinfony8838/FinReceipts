FROM maven:3.9.11-eclipse-temurin-21 AS build
WORKDIR /build
COPY server/pom.xml .
COPY server/src src
RUN --mount=type=cache,target=/root/.m2 mvn -B -Dmaven.test.skip=true package
FROM eclipse-temurin:21-jre
WORKDIR /app
RUN groupadd -g 10001 app && useradd -r -u 10001 -g app app && mkdir /artifacts && chown app:app /artifacts
COPY --from=build /build/target/finreceipts-server-2.0.0-SNAPSHOT.jar /app/server.jar
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["java","-XX:MaxRAMPercentage=70","-jar","/app/server.jar"]
