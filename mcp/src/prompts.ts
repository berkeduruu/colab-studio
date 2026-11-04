import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

export function registerPrompts(server: McpServer): void {
  server.prompt(
    "generate_creative_image",
    "Template for creating a detailed prompt and submitting it to Qwen-Image / Noct-Q V4",
    {
      concept: z.string().describe("Core concept or subject of the image"),
      style: z.string().default("photorealistic").describe("Visual style (photorealistic, anime, digital art, cinematic, oil painting)"),
      aspect_ratio: z.enum(["1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9"]).default("2:3"),
    },
    async ({ concept, style, aspect_ratio }) => {
      return {
        messages: [
          {
            role: "user",
            content: {
              type: "text",
              text: `Please generate an image with Colab Studio:\n\nSubject: ${concept}\nVisual Style: ${style}\nAspect Ratio: ${aspect_ratio}\n\nSteps:\n1. First call 'enhance_prompt' to expand the concept into a rich, vivid prompt.\n2. Submit the enhanced prompt with 'generate_image' (using ratio: "${aspect_ratio}").\n3. Set 'wait: true' to retrieve the result and inspect the generated image.`,
            },
          },
        ],
      };
    }
  );

  server.prompt(
    "image_to_3d_workflow",
    "Workflow to take an existing image and convert it into a 3D textured mesh (.glb)",
    {
      image_filename: z.string().describe("Filename of the input image in Colab Studio media (e.g. 'uploads/chair.png')"),
      quality: z.enum(["fast", "balanced", "high"]).default("balanced"),
    },
    async ({ image_filename, quality }) => {
      const resolution = quality === "fast" ? "1024" : quality === "high" ? "2048" : "1536";
      const faces = quality === "fast" ? 100000 : quality === "high" ? 500000 : 200000;

      return {
        messages: [
          {
            role: "user",
            content: {
              type: "text",
              text: `Convert image to 3D mesh:\n\nImage: ${image_filename}\nResolution: ${resolution}\nFaces: ${faces}\nTexture: pbr\n\nCall 'generate_3d' with image_file: "${image_filename}", resolution: "${resolution}", faces: ${faces}, texture: "pbr".`,
            },
          },
        ],
      };
    }
  );
}
