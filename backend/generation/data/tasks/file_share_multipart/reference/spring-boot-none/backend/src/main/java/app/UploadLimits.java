package app;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.multipart.MultipartException;

/** 413 for an upload over the limit, 400 for a body that is not valid multipart. */
@RestControllerAdvice
public class UploadLimits {

    @ExceptionHandler(MaxUploadSizeExceededException.class)
    public ResponseEntity<Object> tooLarge(MaxUploadSizeExceededException e) {
        return Api.error(413, "file too large");
    }

    @ExceptionHandler(MultipartException.class)
    public ResponseEntity<Object> malformed(MultipartException e) {
        return Api.error(400, "file is required");
    }
}
