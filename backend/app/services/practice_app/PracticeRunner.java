// Checklist runner for a Java practice app (a single Main.java, helpers as
// nested static classes: Main.UI, Main.API, Main.Database, Main.setup()).
// Run as a separate process by practice_app.checker, with the app compiled
// into its own directory. Every checklist loads the app in a fresh class
// loader, so it starts from the app's initial data.
//
// Job file format (written by checker.py - no JSON library needed here):
//   S<TAB>checklist id
//   C<TAB>call<TAB>save_as (may be empty)<TAB>arg<TAB>arg...
// Args: s:<base64 UTF-8> | i:<int> | d:<double> | b:true|false | n (null) | r:<base64 ref path>
// Output: the same JSON result list runner.py prints.
import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class PracticeRunner {

    static String snake(String name) {
        return name.replaceAll("([a-z0-9])([A-Z])", "$1_$2").toLowerCase();
    }

    static String camel(String name) {
        StringBuilder out = new StringBuilder();
        boolean up = false;
        for (char c : name.toCharArray()) {
            if (c == '_') { up = true; continue; }
            out.append(up ? Character.toUpperCase(c) : c);
            up = false;
        }
        return out.toString();
    }

    static String decode(String b64) {
        return new String(Base64.getDecoder().decode(b64), StandardCharsets.UTF_8);
    }

    static String quote(String s) {
        StringBuilder out = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) out.append(String.format("\\u%04x", (int) c)); else out.append(c);
            }
        }
        return out.append('"').toString();
    }

    static String json(Object v) throws Exception {
        if (v == null) return "null";
        if (v instanceof String || v instanceof Character) return quote(v.toString());
        if (v instanceof Boolean) return v.toString();
        if (v instanceof Double || v instanceof Float) {
            double d = ((Number) v).doubleValue();
            return d == Math.rint(d) && !Double.isInfinite(d) ? String.valueOf((long) d) : String.valueOf(d);
        }
        if (v instanceof Number) return v.toString();
        if (v instanceof Enum) return quote(((Enum<?>) v).name());
        if (v instanceof Map) {
            List<String> parts = new ArrayList<>();
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) parts.add(quote(snake(String.valueOf(e.getKey()))) + ":" + json(e.getValue()));
            return "{" + String.join(",", parts) + "}";
        }
        if (v instanceof Iterable) {
            List<String> parts = new ArrayList<>();
            for (Object o : (Iterable<?>) v) parts.add(json(o));
            return "[" + String.join(",", parts) + "]";
        }
        if (v.getClass().isArray()) {
            List<String> parts = new ArrayList<>();
            for (int i = 0; i < Array.getLength(v); i++) parts.add(json(Array.get(v, i)));
            return "[" + String.join(",", parts) + "]";
        }
        List<String> parts = new ArrayList<>();
        for (Class<?> c = v.getClass(); c != null && c != Object.class; c = c.getSuperclass()) {
            for (Field f : c.getDeclaredFields()) {
                if (Modifier.isStatic(f.getModifiers()) || f.isSynthetic()) continue;
                f.setAccessible(true);
                parts.add(quote(snake(f.getName())) + ":" + json(f.get(v)));
            }
        }
        return "{" + String.join(",", parts) + "}";
    }

    static Object member(Object obj, String name) throws Exception {
        if (obj == null) return null;
        if (name.matches("\\d+")) {  // "results.0.id" - an item of a returned list
            int index = Integer.parseInt(name);
            if (obj instanceof List) return index < ((List<?>) obj).size() ? ((List<?>) obj).get(index) : null;
            if (obj.getClass().isArray()) return index < Array.getLength(obj) ? Array.get(obj, index) : null;
        }
        if (obj instanceof Map) {
            Map<?, ?> m = (Map<?, ?>) obj;
            return m.containsKey(name) ? m.get(name) : m.get(camel(name));
        }
        for (Class<?> c = obj.getClass(); c != null && c != Object.class; c = c.getSuperclass()) {
            for (String candidate : new String[] {name, camel(name)}) {
                try {
                    Field f = c.getDeclaredField(candidate);
                    f.setAccessible(true);
                    return f.get(obj);
                } catch (NoSuchFieldException ignored) { }
            }
        }
        return null;
    }

    static Object arg(String token, Map<String, Object> saved) throws Exception {
        if (token.equals("n")) return null;
        String kind = token.substring(0, 1);
        String body = token.substring(2);
        switch (kind) {
            case "s": return decode(body);
            case "i": return Long.parseLong(body);
            case "d": return Double.parseDouble(body);
            case "b": return Boolean.parseBoolean(body);
            case "r": {
                String[] path = decode(body).split("\\.");
                Object value = saved.get(path[0]);
                for (int i = 1; i < path.length; i++) value = member(value, path[i]);
                return value;
            }
            default: throw new IllegalArgumentException("bad arg " + token);
        }
    }

    static Object convert(Object value, Class<?> type) {
        if (value == null) {
            if (type.isPrimitive()) throw new IllegalArgumentException("null passed where " + type.getName() + " is required");
            return null;
        }
        if (type == int.class || type == Integer.class) return ((Number) value).intValue();
        if (type == long.class || type == Long.class) return ((Number) value).longValue();
        if (type == double.class || type == Double.class) return ((Number) value).doubleValue();
        if (type == float.class || type == Float.class) return ((Number) value).floatValue();
        if (type == boolean.class || type == Boolean.class) return value;
        if (type == String.class) return value instanceof String ? value : String.valueOf(value);
        return value;
    }

    static Object call(ClassLoader loader, String target, List<Object> args) throws Throwable {
        String[] parts = target.split("\\.");
        String className = "Main";
        for (int i = 0; i < parts.length - 1; i++) className += "$" + parts[i];
        Class<?> owner;
        try {
            owner = Class.forName(className, true, loader);
        } catch (ClassNotFoundException e) {
            // An engine-built app: its helper groups are top-level classes (UI, API...) next to Main.
            if (parts.length < 2) throw e;
            owner = Class.forName(String.join("$", java.util.Arrays.copyOf(parts, parts.length - 1)), true, loader);
        }
        String name = parts[parts.length - 1];
        for (Method m : owner.getDeclaredMethods()) {
            if (!(m.getName().equals(name) || m.getName().equals(camel(name)))) continue;
            if (!Modifier.isStatic(m.getModifiers()) || m.getParameterCount() != args.size()) continue;
            Object[] converted = new Object[args.size()];
            Class<?>[] types = m.getParameterTypes();
            for (int i = 0; i < types.length; i++) converted[i] = convert(args.get(i), types[i]);
            m.setAccessible(true);
            try {
                return m.invoke(null, converted);
            } catch (InvocationTargetException e) {
                throw e.getCause();
            }
        }
        throw new NoSuchMethodException(target + " with " + args.size() + " argument(s) is not in this app");
    }

    public static void main(String[] argv) throws Exception {
        URL appDir = Paths.get(argv[0]).toUri().toURL();
        List<String> lines = Files.readAllLines(Paths.get(argv[1]), StandardCharsets.UTF_8);
        List<String> out = new ArrayList<>();
        int i = 0;
        while (i < lines.size()) {
            String id = lines.get(i).split("\t", 2)[1];
            i++;
            List<String> steps = new ArrayList<>();
            Map<String, Object> saved = new HashMap<>();
            boolean failed = false;
            try (URLClassLoader loader = new URLClassLoader(new URL[] {appDir}, ClassLoader.getPlatformClassLoader())) {
                while (i < lines.size() && lines.get(i).startsWith("C\t")) {
                    String[] f = lines.get(i).split("\t", -1);
                    i++;
                    if (failed) continue;
                    try {
                        List<Object> args = new ArrayList<>();
                        for (int a = 3; a < f.length; a++) args.add(arg(f[a], saved));
                        Object value = call(loader, f[1], args);
                        if (!f[2].isEmpty()) saved.put(f[2], value);
                        steps.add("{\"value\":" + json(value) + "}");
                    } catch (Throwable e) {
                        steps.add("{\"error\":" + quote(e.getClass().getSimpleName() + ": " + e.getMessage()) + "}");
                        failed = true;
                    }
                }
            }
            out.add("{\"id\":" + quote(id) + ",\"steps\":[" + String.join(",", steps) + "]}");
        }
        System.out.print("[" + String.join(",", out) + "]");
    }
}
