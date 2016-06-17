#!/usr/bin/env python
# -*- encoding: utf-8 -*-
from __future__ import unicode_literals
from builtins import range
import re
import bindings as bi


class JavaTypeTranslator(bi.TypeTranslator):
    def __init__(self):
        bi.TypeTranslator.__init__(self)
        self.types["string"] = "String"


def translate_type(h2o_type, schema):
    return type_adapter.translate(h2o_type, schema)


def get_java_value(field):
    value = field["value"]
    h2o_type = field["type"]
    java_type = translate_type(h2o_type, field["schema_name"])

    if java_type == "float" and value == "Infinity": return "Float.POSITIVE_INFINITY"
    if java_type == "double" and value == "Infinity": return "Double.POSITIVE_INFINITY"
    if java_type == "long": return str(value) + "L"
    if java_type == "float": return str(value) + "f"
    if java_type == "boolean": return str(value).lower()
    if java_type == "String" and (value == "" or value is None): return '""'
    if java_type == "String": return '"%s"' % value
    if value is None: return "null"
    if h2o_type.startswith("enum"): return field["schema_name"] + "." + value
    if h2o_type.endswith("[][]"): return "null"  # TODO
    if h2o_type.endswith("[]"):
        basetype = field["schema_name"] if field["is_schema"] else h2o_type.partition("[")[0]
        if basetype == "Iced": basetype = "Object"
        return "new %s[]{%s}" % (basetype, str(value)[1:-1])
    if h2o_type.startswith("Map"): return "null"  # TODO: handle Map
    if h2o_type.startswith("Key"): return "null"  # TODO: handle Key
    return value

def translate_name(name):
    """
    Converts names with underscores into camelcase. For example:
        "num_rows" => "numRows"
        "very_long_json_name" => "veryLongJsonName"
        "build_GBM_model" => "buildGbmModel"
        "KEY" => "key"
        "middle___underscores" => "middleUnderscores"
        "_exclude_fields" => "_excludeFields" (retain initial/trailing underscores)
        "__http_status__" => "__httpStatus__"
    """
    parts = name.split("_")
    i = 0
    while parts[i] == "":
        parts[i] = "_"
        i += 1
    parts[i] = parts[i].lower()
    for j in range(i+1, len(parts)):
        parts[j] = parts[j].capitalize()
    i = len(parts) - 1
    while parts[i] == "":
        parts[i] = "_"
        i -= 1
    return "".join(parts)


# -----------------------------------------------------------------------------------------------------------------------
# Generate Schema POJOs
# -----------------------------------------------------------------------------------------------------------------------
def generate_schema(class_name, schema):
    """
    Generate schema POJO file.
      :param class_name: name of the class
      :param schema: information about the class
    """
    has_map = False
    is_model_builder = False
    has_inherited = False
    for field in schema["fields"]:
        if field["name"] == "__meta": continue
        if field["is_inherited"]:
            has_inherited = True
            continue
        if field["type"].startswith("Map"): has_map = True
        if field["name"] == "can_build": is_model_builder = True

    superclass = schema["superclass"]
    if superclass == "Schema": superclass = "Object"

    fields = []
    for field in schema["fields"]:
        if field["name"] == "__meta": continue
        java_type = translate_type(field["type"], field["schema_name"])
        java_value = get_java_value(field)

        # hackery: we flatten the parameters up into the ModelBuilder schema, rather than nesting them in the
        # parameters schema class...
        if False and is_model_builder and field["name"] == "parameters":
            fields.append(("parameters", "null", "ModelParameterSchemaV3[]", field["help"], field["is_inherited"]))
        else:
            fields.append((field["name"], java_value, java_type, field["help"], field["is_inherited"]))

    yield "/**"
    yield " * This file is auto-generated by h2o-3/h2o-bindings/bin/gen_java.py"
    yield " * Copyright 2016 H2O.ai;  Apache License Version 2.0 (see LICENSE for details)"
    yield " */"
    yield "package water.bindings.pojos;"
    yield ""
    yield "import com.google.gson.Gson;"
    yield "import com.google.gson.annotations.*;"
    yield "import java.util.Map;" if has_map else None
    yield ""
    yield ""
    yield "public class %s extends %s {" % (class_name, superclass) if superclass != "Object" else None
    yield "public class %s {" % (class_name) if superclass == "Object" else None
    yield ""
    for name, value, ftype, fhelp, inherited in fields:
        if inherited: continue
        ccname = translate_name(name)
        yield "    /**"
        yield bi.wrap(fhelp, indent="     * ")
        yield "     */"
        yield "    @SerializedName(\"%s\")" % name  if name != ccname else None
        yield "    public %s %s;" % (ftype, ccname)
        yield ""
    if has_inherited:
        yield ""
        yield "    /*" + ("-" * 114)
        yield "    //" + (" " * 50) + "INHERITED"
        yield "    //" + ("-" * 114)
        yield ""
        for name, value, ftype, fhelp, inherited in fields:
            if not inherited: continue
            yield bi.wrap(fhelp, "    // ")
            yield "    public %s %s;" % (ftype, translate_name(name))
            yield ""
        yield "    */"
        yield ""
    yield "    /**"
    yield "     * Public constructor"
    yield "     */"
    yield "    public %s() {" % class_name
    for name, value, _, _, _ in fields:
        if name == "parameters": continue
        if value == "null": continue
        yield "        %s = %s;" % (translate_name(name), value)
    yield "    }"
    yield ""
    yield "    /**"
    yield "     * Return the contents of this object as a JSON String."
    yield "     */"
    yield "    @Override"
    yield "    public String toString() {"
    yield "        return new Gson().toJson(this);"
    yield "    }"
    yield ""
    yield "}"


# -----------------------------------------------------------------------------------------------------------------------
# Generate Enum classes
# -----------------------------------------------------------------------------------------------------------------------
def generate_enum(name, values):
    yield "/**"
    yield " * This file is auto-generated by h2o-3/h2o-bindings/bin/gen_java.py"
    yield " * Copyright 2016 H2O.ai;  Apache License Version 2.0 (see LICENSE for details)"
    yield " */"
    yield "package water.bindings.pojos;"
    yield ""
    yield "public enum " + name + " {"
    for value in values:
        yield "    %s," % value
    yield "}"


# -----------------------------------------------------------------------------------------------------------------------
#  Generate Retrofit proxy classes
# -----------------------------------------------------------------------------------------------------------------------
def generate_proxy(classname, endpoints):
    """
    Retrofit interfaces look like this:
        public interface GitHubService {
            @GET("/users/{user}/repos")
            Call<List<Repo>> listRepos(@Path("user") String user);
        }
      :param classname: name of the class
      :param endpoints: list of endpoints served by this class
    """

    # Replace path vars like (?<schemaname>.*) with {schemaname} for Retrofit's annotation
    var_pattern = re.compile(r"\{(\w+)\}")

    helper_class = []
    found_key_array_parameter = False

    yield "/**"
    yield " * This file is auto-generated by h2o-3/h2o-bindings/bin/gen_java.py"
    yield " * Copyright 2016 H2O.ai;  Apache License Version 2.0 (see LICENSE for details)"
    yield " */"
    yield "package water.bindings.proxies.retrofit;"
    yield ""
    yield "import water.bindings.pojos.*;"
    yield "import retrofit2.*;"
    yield "import retrofit2.http.*;"
    yield ""
    yield "public interface " + classname + " {"
    yield ""

    for e in endpoints:
        method = e["handler_method"]

        param_strs = []
        required_param_strs = []
        for field in e["input_params"]:
            fname = field["name"]
            ftype = "Path" if field["is_path_param"] else "Field"
            ptype = translate_type(field["type"], field["schema_name"])
            if ptype.endswith("KeyV3") or ptype == "ColSpecifierV3": ptype = "String"
            if ptype.endswith("KeyV3[]"): ptype = "String[]"
            param_str = "@{ftype}(\"{fname}\") {ptype} {fname}".format(**locals())
            param_strs.append(param_str)
            if field["required"]:
                required_param_strs.append(param_str)
        if len(param_strs) == len(required_param_strs): required_param_strs = None

        yield u"  /** "
        yield bi.wrap(e["summary"], indent="   * ")
        for field in e["input_params"]:
            s = "   *   @param %s " % field["name"]
            yield s + bi.wrap(field["help"], indent="   *"+" "*(len(s)-4), indent_first=False)
        yield u"   */"
        # Create 2 versions of each call: first with all input parameters present, and then only with required params
        for params in [param_strs, required_param_strs]:
            if params is None: continue
            yield u"  @FormUrlEncoded" if e["http_method"] == "POST" else None
            yield u"  @{method}(\"{path}\")".format(method=e["http_method"], path=e["url_pattern"])
            if len(params) <= 1:
                args = params[0] if params else ""
                yield "  Call<{schema}> {method}({args});".format(schema=e["output_schema"], method=method, args=args)
            else:
                yield "  Call<{schema}> {method}(".format(schema=e["output_schema"], method=method)
                for arg in params:
                    yield "    " + arg + ("" if arg == params[-1] else ",")
                yield "  );"
            yield ""

        # Make special static Helper class for Grid and ModelBuilders.
        if "algo" in e:
            # We make two train_ and validate_ methods.  One (built here) takes the parameters schema, the other
            # (built above) takes each parameter.
            helper_class.append("    /**")
            helper_class.append(bi.wrap(e["summary"], indent="     * "))
            helper_class.append("     */")
            helper_class.append("    public static Call<{oschema}> {method}({outer_class} z, {ischema} p) {{"
                                .format(ischema=e["input_schema"], oschema=e["output_schema"], method=method,
                                        outer_class=classname))
            helper_class.append("      return z.{method}(".format(method=method))
            for field in e["input_params"]:
                ptype = translate_type(field["type"], field["schema_name"])
                pname = translate_name(field["name"])
                if ptype.endswith("KeyV3"):
                    s = "(p.{parm} == null? null : p.{parm}.name)".format(parm=pname)
                elif ptype.endswith("KeyV3[]"):
                    found_key_array_parameter = True
                    s = "(p.{parm} == null? null : keyArrayToStringArray(p.{parm}))".format(parm=pname)
                elif ptype.startswith("ColSpecifier"):
                    s = "(p.{parm} == null? null : p.{parm}.columnName)".format(parm=pname)
                else:
                    s = "p." + pname
                if field != e["input_params"][-1]:
                    s += ","
                helper_class.append("        " + s)
            helper_class.append("      );")
            helper_class.append("    }")
            helper_class.append("")

    if helper_class:
        yield ""
        yield "  class Helper {"
        for line in helper_class:
            yield line
        if found_key_array_parameter:
            yield "    /**"
            yield "     * Return an array of Strings for an array of keys."
            yield "     */"
            yield "    public static String[] keyArrayToStringArray(KeyV3[] keys) {"
            yield "      if (keys == null) return null;"
            yield "      String[] ids = new String[keys.length];"
            yield "      int i = 0;"
            yield "      for (KeyV3 key : keys) ids[i++] = key.name;"
            yield "      return ids;"
            yield "    }"
        yield "  }"
        yield ""

    yield "}"


# -----------------------------------------------------------------------------------------------------------------------
#  Generate main Retrofit interface class
# -----------------------------------------------------------------------------------------------------------------------
def generate_main_class(endpoints):
    yield "/**"
    yield " * This file is auto-generated by h2o-3/h2o-bindings/bin/gen_java.py"
    yield " * Copyright 2016 H2O.ai;  Apache License Version 2.0 (see LICENSE for details)"
    yield " */"
    yield "package water.bindings;"
    yield ""
    yield "import water.bindings.pojos.*;"
    yield "import water.bindings.proxies.retrofit.*;"
    yield "import retrofit2.*;"
    yield "import retrofit2.converter.gson.GsonConverterFactory;"
    yield "import com.google.gson.*;"
    yield "import okhttp3.OkHttpClient;"
    yield "import java.io.IOException;"
    yield "import java.lang.reflect.Type;"
    yield "import java.util.concurrent.TimeUnit;"
    yield ""
    yield "public class H2oApi {"
    yield ""
    yield "  public H2oApi() {}"
    yield "  public H2oApi(String url) { this.url = url; }"
    yield ""
    yield "  public H2oApi setUrl(String s) {"
    yield "    url = s;"
    yield "    retrofit = null;"
    yield "    return this;"
    yield "  }"
    yield ""
    yield "  public H2oApi setTimeout(int t) {"
    yield "    timeout_s = t;"
    yield "    retrofit = null;"
    yield "    return this;"
    yield "  }"
    yield ""
    yield "  /**"
    yield "   * Set time interval for job polling in {@link #waitForJobCompletion(JobKeyV3)}."
    yield "   *   @param millis time interval, in milliseconds"
    yield "   */"
    yield "  public H2oApi setJobPollInterval(int millis) {"
    yield "    pollInterval_ms = millis;"
    yield "    return this;"
    yield "  }"
    yield ""
    yield "  /**"
    yield "   * Continuously poll server for the status of the given job, until it completes."
    yield "   *   @param jobKey job to query"
    yield "   *   @return the finished job"
    yield "   */"
    yield "  public JobV3 waitForJobCompletion(JobKeyV3 jobKey) {"
    yield "    return waitForJobCompletion(keyToString(jobKey));"
    yield "  }"
    yield "  public JobV3 waitForJobCompletion(String jobId) {"
    yield "    Jobs jobService = getService(Jobs.class);"
    yield "    Response<JobsV3> jobsResponse = null;"
    yield "    int retries = 3;"
    yield "    JobsV3 jobs = null;"
    yield "    do {"
    yield "      try {"
    yield "        Thread.sleep(pollInterval_ms);"
    yield "        jobsResponse = jobService.fetch(jobId).execute();"
    yield "      } catch (IOException e) {"
    yield "        System.err.println(\"Caught exception: \" + e);"
    yield "      } catch (InterruptedException e) { /* pass */ }"
    yield "      if (jobsResponse == null || !jobsResponse.isSuccessful())"
    yield "        if (retries-- > 0)"
    yield "          continue;"
    yield "        else"
    yield "          throw new RuntimeException(\"/3/Jobs/\" + jobId + \" failed 3 times.\");"
    yield "      jobs = jobsResponse.body();"
    yield "      if (jobs.jobs == null || jobs.jobs.length != 1)"
    yield "        throw new RuntimeException(\"Failed to find Job: \" + jobId);"
    yield "    } while (jobs != null && jobs.jobs[0].status.equals(\"RUNNING\"));"
    yield "    return jobs == null? null : jobs.jobs[0];"
    yield "  }"
    yield ""

    for route in endpoints:
        apiname = route["api_name"]
        class_name = route["class_name"]
        outtype = route["output_schema"]
        input_fields = route["input_params"]
        required_fields = [field  for field in input_fields if field["required"]]
        input_fields_wo_excluded = [field  for field in input_fields if field["name"] != "_exclude_fields"]

        yield "  /**"
        yield bi.wrap(route["summary"], indent="   * ")
        yield "   */"
        # Make several versions of each API call:
        #  (1) Only the required parameters
        #  (2) All parameters except the _excluded_fields
        #  (3) All parameters
        li = len(input_fields)
        le = len(input_fields_wo_excluded)
        lr = len(required_fields)
        assert lr <= 3, "Too many required fields in method " + apiName
        if lr == li:
            # The set of required fields is the same as the set of input fields. No need for (2) and (3).
            input_fields = None
            input_fields_wo_excluded = None
        elif le == li or le == lr or li >= 4:
            # If set (2) coincides with either (1) or (3), then we will not generate code for it.
            # Additionally, if there are too many input params so that we will put them into a container class,
            # then there will be no need for separate case (2) either.
            input_fields_wo_excluded = None

        for fields in [required_fields, input_fields_wo_excluded, input_fields]:
            if fields is None: continue
            use_schema_param = (len(fields) >= 4)
            value_field_strs = []
            typed_field_strs = []
            for field in fields:
                ftype = translate_type(field["type"], field["schema_name"])
                fname = translate_name(field["name"])
                typed_field_strs.append("%s %s" % (ftype, fname))
                if use_schema_param: fname = "params." + fname
                if ftype.endswith("KeyV3"):
                    s = "keyToString(%s)" % fname
                elif ftype.endswith("KeyV3[]"):
                    s = "keyArrayToStringArray(%s)" % fname
                elif ftype.startswith("ColSpecifier"):
                    s = "colToString(%s)" % fname
                else:
                    s = fname
                value_field_strs.append(s)

            if use_schema_param:
                args = route["input_schema"] + " params"
                values = "\n      " + ",\n      ".join(value_field_strs) + "\n    "
            else:
                args = ", ".join(typed_field_strs)
                values = ", ".join(value_field_strs)
                if fields == input_fields_wo_excluded:
                    values += ", \"\""

            yield "  public {type} {method}({args}) throws IOException {{".\
                  format(type=outtype, method=apiname, args=args)
            yield "    {clazz} s = getService({clazz}.class);".format(clazz=class_name)
            yield "    return s.{method}({values}).execute().body();".\
                  format(method=route["handler_method"], values=values);
            yield "  }"
        yield ""

    yield ""
    yield "  //--------- PRIVATE " + "-"*98
    yield ""
    yield "  private Retrofit retrofit;"
    yield "  private String url = \"http://localhost/54321/\";"
    yield "  private int timeout_s = 60;"
    yield "  private int pollInterval_ms = 1000;"
    yield ""
    yield "  private void initializeRetrofit() {"
    yield "    Gson gson = new GsonBuilder()"
    yield "      .registerTypeAdapter(KeyV3.class, new KeySerializer())"
    yield "      .registerTypeAdapter(ColSpecifierV3.class, new ColSerializer())"
    yield "      .registerTypeAdapter(ModelsV3.class, new ModelDeserializer())"
    yield "      .create();"
    yield ""
    yield "    OkHttpClient client = new OkHttpClient.Builder()"
    yield "      .connectTimeout(timeout_s, TimeUnit.SECONDS)"
    yield "      .writeTimeout(timeout_s, TimeUnit.SECONDS)"
    yield "      .readTimeout(timeout_s, TimeUnit.SECONDS)"
    yield "      .build();"
    yield ""
    yield "    this.retrofit = new Retrofit.Builder()"
    yield "      .client(client)"
    yield "      .baseUrl(url)"
    yield "      .addConverterFactory(GsonConverterFactory.create(gson))"
    yield "      .build();"
    yield "  }"
    yield ""
    yield "  private Retrofit getRetrofit() {"
    yield "    if (retrofit == null) initializeRetrofit();"
    yield "    return retrofit;"
    yield "  }"
    yield ""
    yield "  private <T> T getService(Class<T> clazz) {"
    yield "    return getRetrofit().create(clazz);"
    yield "  }"
    yield ""
    yield ""
    yield "  /**"
    yield "   * Keys get sent as Strings and returned as objects also containing the type and URL,"
    yield "   * so they need a custom GSON serializer."
    yield "   */"
    yield "  private static class KeySerializer implements JsonSerializer<KeyV3> {"
    yield "    @Override"
    yield "    public JsonElement serialize(KeyV3 key, Type typeOfKey, JsonSerializationContext context) {"
    yield "      return new JsonPrimitive(key.name);"
    yield "    }"
    yield "  }"
    yield "  private static class ColSerializer implements JsonSerializer<ColSpecifierV3> {"
    yield "    @Override"
    yield "    public JsonElement serialize(ColSpecifierV3 col, Type typeOfCol, JsonSerializationContext context) {"
    yield "      return new JsonPrimitive(col.columnName);"
    yield "    }"
    yield "  }"
    yield "  /**"
    yield "   * Factory method for parsing a ModelsV3 json object into an instance of the model-specific subclass."
    yield "   */"
    yield "  private static class ModelDeserializer implements JsonDeserializer<ModelsV3> {"
    yield "    @Override"
    yield "    public ModelsV3 deserialize(JsonElement json, Type typeOfT, JsonDeserializationContext context)"
    yield "      throws JsonParseException {"
    yield "      if (json.isJsonNull()) return null;"
    yield "      if (json.isJsonObject()) {"
    yield "        JsonObject jobj = json.getAsJsonObject();"
    yield "        if (jobj.has(\"algo\")) {"
    yield "          String algo = jobj.get(\"algo\").getAsJsonPrimitive().getAsString().toLowerCase();"
    yield "          switch (algo) {"
    for route in endpoints:
        if route["class_name"] == "ModelBuilders" and route["api_name"].startswith("train"):
            algo = route["algo"]
            oschema = route["output_schema"]
            assert oschema.lower()[:len(algo)] == algo, "Wrong output schema for algo %s: %s" % (algo, oschema)
            model = oschema[:len(algo)] + "Model" + oschema[len(algo):]  # "DeepLearningV3" => "DeepLearningModelV3"
            yield "            case \"{algo}\": return context.deserialize(json, {model}.class);".format(**locals())
    yield "            default:"
    yield "              throw new JsonParseException(\"Unable to deserialize model of type \" + algo);"
    yield "          }"
    yield "        }"
    yield "      }"
    yield "      throw new JsonParseException(\"Invalid ModelsV3 element \" + json.toString());"
    yield "    }"
    yield "  }"
    yield ""
    yield "  /**"
    yield "   * Return an array of Strings for an array of keys."
    yield "   */"
    yield "  private static String[] keyArrayToStringArray(KeyV3[] keys) {"
    yield "    if (keys == null) return null;"
    yield "    String[] ids = new String[keys.length];"
    yield "    int i = 0;"
    yield "    for (KeyV3 key : keys) ids[i++] = key.name;"
    yield "    return ids;"
    yield "  }"
    yield ""
    yield "  /**"
    yield "   *"
    yield "   */"
    yield "  private static String keyToString(KeyV3 key) {"
    yield "    return key == null? null : key.name;"
    yield "  }"
    yield ""
    yield "  /**"
    yield "   *"
    yield "   */"
    yield "  private static String colToString(ColSpecifierV3 col) {"
    yield "    return col == null? null : col.columnName;"
    yield "  }"
    yield ""
    yield "}"


# -----------------------------------------------------------------------------------------------------------------------
# MAIN:
# -----------------------------------------------------------------------------------------------------------------------
def main():
    bi.init("Java", "java")

    for schema in bi.schemas():
        name = schema["name"]
        bi.vprint("Generating schema: " + name)
        bi.write_to_file("water/bindings/pojos/%s.java" % name, generate_schema(name, schema))

    for name, values in bi.enums().items():
        bi.vprint("Generating enum: " + name)
        bi.write_to_file("water/bindings/pojos/%s.java" % name, generate_enum(name, sorted(values)))

    for name, endpoints in bi.endpoint_groups().items():
        bi.vprint("Generating proxy: " + name)
        bi.write_to_file("water/bindings/proxies/retrofit/%s.java" % name, generate_proxy(name, endpoints))

    bi.vprint("Generating H2oApi.java")
    bi.write_to_file("water/bindings/H2oApi.java", generate_main_class(bi.endpoints()))

    type_adapter.vprint_translation_map()


if __name__ == "__main__":
    type_adapter = JavaTypeTranslator()
    main()
