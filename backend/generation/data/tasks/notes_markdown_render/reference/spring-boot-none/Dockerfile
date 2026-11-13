FROM maven:3.9-eclipse-temurin-21@sha256:99e61abcff91a9b1333463bd8451fb18495d6eba9250ac66a338b518f8278320 AS build
WORKDIR /src
COPY backend/pom.xml backend/
RUN cd backend && mvn -B -q dependency:resolve
COPY backend/ backend/
RUN mkdir -p /out \
    && cd backend \
    && mvn -B -q -DskipTests package \
    && cp "$(find target -maxdepth 1 -name '*.jar' ! -name '*-sources.jar' ! -name '*-javadoc.jar' | sort | head -n 1)" /out/app.jar

FROM eclipse-temurin:21-jre-noble@sha256:0eda5830ed7b669abc246a8dc520ea8a23cab06f9a8d4b6964109e2f2394e46f
ENV PORT=8000
WORKDIR /app
COPY --from=build /out/ ./
RUN mkdir -p data
EXPOSE 8000
CMD ["java", "-jar", "app.jar"]
