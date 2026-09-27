import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import org.json.JSONObject;

public class Main {
    public static void main(String[] args) throws Exception {
        String apiUrl = System.getenv("PRACTICE_API_URL");
        
        JSONObject requestBody = new JSONObject();
        requestBody.put("email", "testuser@library.test");
        requestBody.put("password", "Test@123");
        
        HttpClient client = HttpClient.newHttpClient();
        HttpRequest request = HttpRequest.newBuilder()
            .uri(new URI(apiUrl + "login"))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(requestBody.toString()))
            .build();
        
        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
        
        if (response.statusCode() == 200) {
            System.out.println("Status code 200: PASS");
        } else {
            System.out.println("Status code 200: FAIL (got " + response.statusCode() + ")");
        }
        
        JSONObject responseBody = new JSONObject(response.body());
        String name = responseBody.getString("name");
        
        if ("Test User".equals(name)) {
            System.out.println("Name is Test User: PASS");
        } else {
            System.out.println("Name is Test User: FAIL (got " + name + ")");
        }
    }
    
    static void incomplete(String step) {
        System.out.println("INCOMPLETE: " + step);
        System.exit(3);
    }
}