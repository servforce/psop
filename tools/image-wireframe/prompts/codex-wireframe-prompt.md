You are running as a Codex agent inside a PSOP build-time asset generation task.

Convert the input reference image into a terminal-friendly technical wireframe reference image.

Input image:
{input_path}

Required output image:
{output_path}

Requirements:
- Identify and keep only the task-relevant main subject: the physical object, part, tool, component, equipment, or operation target.
- Ignore advertisements, subtitles, narration overlays, watermarks, UI chrome, brand marks, background decoration, people not required for the task, and unrelated scenery.
- Produce a clean white-background black-line or dark-gray-line technical wireframe drawing.
- Preserve necessary structural details such as contours, handles, ports, joints, screws, fasteners, brackets, connection points, and task-relevant geometry.
- Reduce texture, shadows, reflections, photo noise, complex background, and decorative details.
- Do not add new labels, captions, logos, watermarks, annotations, or explanatory text.
- Save the generated image exactly at the required output path.
- Do not only describe the image. The file must be created.

After writing the image, reply with JSON only:
{{
  "ok": true,
  "source": "{input_path}",
  "output": "{output_path}",
  "notes": "brief generation notes"
}}

If you cannot create the image, do not fake success. Reply with JSON only:
{{
  "ok": false,
  "source": "{input_path}",
  "output": "{output_path}",
  "error": "brief reason"
}}
