# Callback protocol: grokbot-video-bridge/1

The local MCP server creates a job and sends its instruction payload to the configured Grok Bot webhook, authenticated with `Authorization: Bearer <webhook_key>`.

```json
{
  "protocol": "grokbot-video-bridge/1",
  "job_id": "0123456789abcdef0123456789abcdef",
  "prompt": "Animate the supplied reference",
  "duration_seconds": 6,
  "aspect_ratio": "16:9",
  "silent": true,
  "image_urls": ["https://TUNNEL.trycloudflare.com/jobs/JOB_ID/images/0?token=READ_TOKEN"],
  "status_url": "https://TUNNEL.trycloudflare.com/jobs/JOB_ID/status?token=WRITE_TOKEN",
  "result_upload_url": "https://TUNNEL.trycloudflare.com/jobs/JOB_ID/video?token=WRITE_TOKEN"
}
```

All values above are placeholders. Read and write tokens are independently generated for each job and must not be logged or shared.

| Endpoint | Method | Behavior |
| --- | --- | --- |
| `/healthz` | GET | Minimal tunnel/instance health check; no job data. |
| `/jobs/{id}/images/{index}?token=…` | GET | Return only the designated image for the matching read token. |
| `/jobs/{id}/status?token=…` | POST | Receive JSON `running`, `failed`, or `succeeded` with optional message/error. |
| `/jobs/{id}/video?token=…` | PUT | Receive video bytes with Content-Length; validate with ffprobe, optionally strip audio, atomically publish the result. |

Status JSON must fit within 16 KiB. Reference files are limited to 20 MiB and video uploads to 90 MiB. Job capability links expire after 24 hours.

A success callback before a playable video upload is rejected. Uploading the same completed result is idempotent; a different result cannot overwrite it. An already verified success is not downgraded by a late status message.

`get_video_status` reads the local job database, not an undocumented Grok API. Its transfer events distinguish submission, actual image retrieval and verified video receipt. An interrupted webhook request becomes `submission_unknown`; the bridge does not automatically repeat potentially billable generation.
