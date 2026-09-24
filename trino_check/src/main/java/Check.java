import io.trino.Session;
import io.trino.plugin.memory.MemoryConnectorFactory;
import io.trino.testing.PlanTester;
import java.nio.file.*;
import java.util.*;
import static io.trino.testing.TestingSession.testSessionBuilder;

public class Check {
    public static void main(String[] args) throws Exception {
        String tsType = args[1];
        Session s = testSessionBuilder().setCatalog("memory").setSchema("default")
            .setTimeZoneKey(io.trino.spi.type.TimeZoneKey.UTC_KEY).build();
        PlanTester t = PlanTester.create(s);
        t.createCatalog("memory", new MemoryConnectorFactory(), Map.of());
        int ok = 0, bad = 0;
        List<Path> files = new ArrayList<>();
        try (var st = Files.list(Path.of(args[0]))) { st.filter(p -> p.toString().endsWith(".sql")).sorted().forEach(files::add); }
        for (Path f : files) {
            String sql = Files.readString(f).strip();
            if (sql.endsWith(";")) sql = sql.substring(0, sql.length() - 1);
            final String q = sql;
            try {
                t.inTransaction(ss -> t.createPlan(ss, q));
                ok++;
            } catch (Throwable e) {
                bad++;
                String m = String.valueOf(e.getMessage());
                System.out.println("ERROR " + f.getFileName() + ": " + m.substring(0, Math.min(400, m.length())));
            }
        }
        System.out.println("minuto_utc " + tsType + " -> analizadas OK: " + ok + ", con error: " + bad);
        System.exit(0);
    }
}
