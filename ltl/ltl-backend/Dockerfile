# Stage 1: Build
FROM eclipse-temurin:21-jdk-alpine AS build
WORKDIR /app

COPY gradle/ gradle/
COPY gradlew build.gradle settings.gradle ./
RUN chmod +x gradlew && ./gradlew dependencies --no-daemon

COPY src/ src/
RUN ./gradlew bootJar --no-daemon -x test

# Stage 2: Run
FROM eclipse-temurin:21-jre-alpine
WORKDIR /app

RUN addgroup -S ltl && adduser -S ltl -G ltl
USER ltl

COPY --from=build /app/build/libs/*.jar app.jar

ENV SERVER_PORT=8080
EXPOSE 8080

# Heap is larger than Scooped's: the relay holds a send buffer per
# connected household, so memory scales with customers rather than with
# request concurrency. G1 rather than SerialGC for the same reason —
# thousands of long-lived sockets make pause time matter.
ENTRYPOINT ["java", \
  "-XX:+UseG1GC", \
  "-XX:MaxGCPauseMillis=100", \
  "-Xms384m", \
  "-Xmx1024m", \
  "-Djava.security.egd=file:/dev/./urandom", \
  "-jar", "app.jar"]
