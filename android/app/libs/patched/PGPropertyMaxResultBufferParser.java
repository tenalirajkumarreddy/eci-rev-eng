/*
 * Android-safe variant of pgjdbc 42.7.4's
 * org.postgresql.util.PGPropertyMaxResultBufferParser.
 *
 * Upstream (REL42.7.4) calls java.lang.management.ManagementFactory inside
 * adjustResultSize() and calculatePercentOfMemory(). Android has no
 * java.lang.management, so ANY connection on stock pgjdbc dies with
 * NoClassDefFoundError at PGStream.setMaxResultBuffer() during connect:
 *
 *   FATAL EXCEPTION: schema-init
 *   java.lang.NoClassDefFoundError: java.lang.management.ManagementFactory
 *     at ...PGPropertyMaxResultBufferParser.adjustResultSize(...:197)
 *     at ...PGStream.setMaxResultBuffer(PGStream.java:758)
 *     at ...ConnectionFactoryImpl.tryConnect(...)
 *
 * The class cannot simply be skipped: PGStream references it on every connect.
 * This file is upstream verbatim except the heap-size lookups, replaced by a
 * fixed 512 MB stand-in cap. Only PGStream.setMaxResultBuffer calls this class,
 * and with the maxResultBuffer property unset the parsed value is -1
 * (= unlimited), which never triggers the cap - so behaviour on the default
 * path is byte-for-byte what upstream intends.
 */
package org.postgresql.util;

import java.util.logging.Level;
import java.util.logging.Logger;

public class PGPropertyMaxResultBufferParser {
  private static final Logger LOGGER =
      Logger.getLogger(PGPropertyMaxResultBufferParser.class.getName());
  private static final String[] PERCENT_PHRASES = new String[] {"p", "pct", "percent"};

  /** Stand-in for 90% of Runtime.maxMemory() (java.lang.management is absent on Android). */
  private static final long HEAP_CAP_BYTES = 512L * 1024 * 1024;

  /**
   * Method to parse value of max result buffer size.
   *
   * @param value string containing size of bytes with optional multiplier (T, G, M or K) or
   *     percent value to declare max percent of heap memory to use.
   * @return value of max result buffer size.
   * @throws PSQLException Exception when given value can't be parsed.
   */
  public static long parseProperty(String value) throws PSQLException {
    long result = -1;
    //noinspection StatementWithEmptyBody
    if (value == null) {
      // default branch
    } else if (checkIfValueContainsPercent(value)) {
      result = parseBytePercentValue(value);
    } else if (!value.isEmpty()) {
      result = parseByteValue(value);
    }
    result = adjustResultSize(result);
    return result;
  }

  private static boolean checkIfValueContainsPercent(String value) {
    return getPercentPhraseLengthIfContains(value) != -1;
  }

  private static long parseBytePercentValue(String value) throws PSQLException {
    long result = -1;
    int length;
    if (!value.isEmpty()) {
      length = getPercentPhraseLengthIfContains(value);
      if (length == -1) {
        throwExceptionAboutParsingError(
            "Received MaxResultBuffer parameter can't be parsed. Value received to parse: {0}",
            value);
      }
      result = calculatePercentOfMemory(value, length);
    }
    return result;
  }

  private static int getPercentPhraseLengthIfContains(String valueToCheck) {
    int result = -1;
    for (String phrase : PERCENT_PHRASES) {
      int indx = getPhraseLengthIfContains(valueToCheck, phrase);
      if (indx != -1) {
        result = indx;
      }
    }
    return result;
  }

  private static int getPhraseLengthIfContains(String valueToCheck, String phrase) {
    int searchValueLength = phrase.length();
    if (valueToCheck.length() > searchValueLength) {
      String subValue = valueToCheck.substring(valueToCheck.length() - searchValueLength);
      if (subValue.equals(phrase)) {
        return searchValueLength;
      }
    }
    return -1;
  }

  private static long calculatePercentOfMemory(String value, int percentPhraseLength) {
    String realValue = value.substring(0, value.length() - percentPhraseLength);
    double percent = Double.parseDouble(realValue) / 100;
    return (long) (percent * HEAP_CAP_BYTES);
  }

  private static long parseByteValue(String value) throws PSQLException {
    long result = -1;
    long multiplier = 1;
    long mul = 1000;
    String realValue;
    char sign = value.charAt(value.length() - 1);
    switch (sign) {
      case 'T':
      case 't':
        multiplier *= mul;
        // fall through
      case 'G':
      case 'g':
        multiplier *= mul;
        // fall through
      case 'M':
      case 'm':
        multiplier *= mul;
        // fall through
      case 'K':
      case 'k':
        multiplier *= mul;
        realValue = value.substring(0, value.length() - 1);
        result = Integer.parseInt(realValue) * multiplier;
        break;
      case '%':
        return result;
      default:
        if (sign >= '0' && sign <= '9') {
          result = Long.parseLong(value);
        } else {
          throwExceptionAboutParsingError(
              "Received MaxResultBuffer parameter can't be parsed. Value received to parse: {0}",
              value);
        }
        break;
    }
    return result;
  }

  private static long adjustResultSize(long value) {
    if (value > 0.9 * HEAP_CAP_BYTES) {
      long newResult = (long) (0.9 * HEAP_CAP_BYTES);
      LOGGER.log(
          Level.WARNING,
          GT.tr(
              "WARNING! Required to allocate {0} bytes, which exceeded possible heap memory size. Assigned {1} bytes as limit.",
              String.valueOf(value),
              String.valueOf(newResult)));
      value = newResult;
    }
    return value;
  }

  private static void throwExceptionAboutParsingError(String message, Object... values)
      throws PSQLException {
    throw new PSQLException(GT.tr(message, values), PSQLState.SYNTAX_ERROR);
  }
}
