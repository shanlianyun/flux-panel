package com.admin.common.aop;


import java.lang.annotation.*;

@Target({ElementType.METHOD})
@Retention(RetentionPolicy.RUNTIME)
@Documented
public @interface LogAnnotation {
    /** Log successful requests at DEBUG; failures remain visible. */
    boolean debug() default false;
}
